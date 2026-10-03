"""Parse raw EDGAR HTML into a ParsedFiling with section offsets and table data.

Public API
----------
    parse_filing(html, meta) -> ParsedFiling

Algorithm
---------
1.  ``doc.to_markdown()`` gives the full filing as markdown with tables
    embedded inline at their natural document positions.

2.  For each section key, start position in the markdown is found via a
    fallback chain (tried in order):

    a. **edgartools** – the content anchor (first unique line of
       ``get_sec_section(key, clean=True)``), used only when it agrees with the
       heading / cross-reference position, or has none and is not a ToC row.
    b. **heading** – standalone ``# Item N.`` heading or two-cell
       ``| ITEM N. | Title |`` table row in the document body (2–97 %).
    c. **cross_reference_index** – the filing contains a cross-reference
       index table in the last 12 % of the markdown that maps SEC item
       numbers to the filer's own section titles; those titles are then
       located in the body.

3.  Section texts are slices of the markdown; offsets are constructed so
    ``text[char_start:char_end] == sec_text`` is exact for every section.

4.  ``fallback_sections`` holds keys found via methods b or c (not edgartools).
    ``missing_sections`` holds required keys that no method could find.
"""
from __future__ import annotations

import logging
import re

from edgar.documents import parse_html  # type: ignore[import-untyped]
from edgar.documents.renderers.markdown import (  # type: ignore[import-untyped]
    MarkdownRenderer,
)

from ingest.models import FilingMeta, ParsedFiling, ParsedSection, ParsedTable

logger = logging.getLogger(__name__)

# ── Canonical section order ───────────────────────────────────────────────────

SECTION_ORDER_10K: list[str] = [
    "part_i_item_1", "part_i_item_1a", "part_i_item_1b", "part_i_item_1c",
    "part_i_item_2", "part_i_item_3", "part_i_item_4",
    "part_ii_item_5", "part_ii_item_6", "part_ii_item_7", "part_ii_item_7a",
    "part_ii_item_8", "part_ii_item_9", "part_ii_item_9a", "part_ii_item_9b",
    "part_ii_item_9c", "part_iii_item_10", "part_iii_item_11",
    "part_iii_item_12", "part_iii_item_13", "part_iii_item_14",
    "part_iv_item_15", "part_iv_item_16", "part_iv_signatures",
]

SECTION_ORDER_10Q: list[str] = [
    "part_i_item_1", "part_i_item_2", "part_i_item_3", "part_i_item_4",
    "part_ii_item_1", "part_ii_item_1a", "part_ii_item_2", "part_ii_item_3",
    "part_ii_item_4", "part_ii_item_5", "part_ii_item_6",
]

REQUIRED_SECTIONS_10K: frozenset[str] = frozenset({
    "part_i_item_1a",   # Risk Factors
    "part_ii_item_7",   # MD&A
    "part_ii_item_8",   # Financial Statements
})

REQUIRED_SECTIONS_10Q: frozenset[str] = frozenset({
    "part_i_item_1",    # Financial Statements
    "part_i_item_2",    # MD&A
    "part_ii_item_1a",  # Risk Factors
})

_SEPARATOR = "\n\n"
# Label of the text before the first located section (cover page, ToC, glossary).
# Step 3 decides whether the chunker embeds it; offsets cover it either way.
PREAMBLE_KEY = "preamble"
_MIN_CONTINUATION_TITLE_CHARS = 10
_GENERIC_INDEX_TITLES = frozenset({"none", "not applicable", "n/a"})
# An edgartools content anchor is trusted over a heading or cross-reference
# title only when it lands within this many characters of it.
ANCHOR_TOLERANCE = 400
_BODY_MAX_FRAC = 0.995
_CROSS_REF_TAIL_FRAC = 0.88  # the filer's item cross-reference index sits in this tail
_SIGNATURES_MIN_FRAC = 0.80  # signatures are always in the last 20 % of a filing
_ANCHOR_MIN_LINE_CHARS = 15  # shorter lines are too generic to anchor on
_ANCHOR_MAX_OCCURRENCES = 5  # a line seen more often than this is not unique
_BODY_MIN_FRAC = 0.02  # content anchors ignore the cover page / ToC before this point
_TITLE_PHRASE_CHARS = 35
_MIN_TITLE_WORD_CHARS = 4
_BARE_LINE_MAX_EXTRA = 60
_STATEMENTS_CAPTION = re.compile(
    r"^(?:\| *consolidated(?: condensed)? statements? of (?:operations|income|earnings)"
    r"[^|\n]*\| *"
    r"|#+ +consolidated(?: condensed)? statements? of (?:operations|income|earnings)[^|\n]*)$",
    re.IGNORECASE | re.MULTILINE,
)
_FINANCIAL_STATEMENT_KEYS = frozenset({"part_i_item_1", "part_ii_item_8"})
_ONE_CELL_ROW = re.compile(r"\| *[^|\n]+ *\|")
_PAGE_NUMBER_CELL = re.compile(r"\|\s*(?:\d{1,3}|Page)\s*\|\s*$", re.IGNORECASE)
_LINE_PREFIX_MARKUP = re.compile(r"[#*|_<>/\s]*")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sort_key(key: str, order: list[str]) -> tuple[int, str]:
    """Return (position, key) for sorting; unknown keys sort after all known."""
    try:
        return (order.index(key), key)
    except ValueError:
        return (len(order), key)


def _item_num_from_key(key: str) -> str | None:
    """Extract item number from a canonical key, e.g. 'part_ii_item_7a' → '7a'."""
    m = re.match(r"part_[iv]+_item_(.+)$", key)
    return m.group(1) if m else None


def _find_heading_pos(md: str, key: str, min_pos: int, max_pos: int) -> int | None:
    """Find start position of the section heading for *key* in md[min_pos:max_pos].

    Tries:
    1. Standalone markdown heading: ``# Item N[A-Z]?. ...`` or bold ``**ITEM N\\.**``
    2. Two-cell table row:          ``| Item N[A-Z]?. | Title |``
    3. For ``part_iv_signatures``: bold ``**Signatures**`` or heading ``# Signatures``
    """
    # Special case: signatures section has no item number.
    # Only match an explicit markdown heading or a standalone bold line to
    # avoid false positives from phrases that begin with the word "signatures".
    if key == "part_iv_signatures":
        pat_sig = re.compile(
            r"^(?:#+ +signatures|\*\* *signatures *\*\*) *$",
            re.IGNORECASE | re.MULTILINE,
        )
        search = md[min_pos:max_pos]
        m = pat_sig.search(search)
        return min_pos + m.start() if m else None

    num = _item_num_from_key(key)
    if num is None:
        return None
    num_pat = re.escape(num)

    # Standalone: line starts with # chars, then "Item N." or "Item N " etc.
    pat_heading = re.compile(
        rf"^(?:#+ +item +{num_pat}[.\s]|\*\*item +{num_pat}\\?\.)",
        re.IGNORECASE | re.MULTILINE,
    )
    # Two-cell row: | Item N. | non-pipe text |  (no third cell)
    pat_2cell = re.compile(
        rf"^\| *item +{num_pat}\.? *\| *[^|\n]+ *\| *$",
        re.IGNORECASE | re.MULTILINE,
    )

    search = md[min_pos:max_pos]
    candidates: list[int] = [
        min_pos + hit.start() for hit in pat_heading.finditer(search)
    ] + [
        min_pos + hit.start() for hit in pat_2cell.finditer(search)
    ]
    return min(candidates) if candidates else None


def _line_start(md: str, pos: int) -> int:
    return md.rfind("\n", 0, pos) + 1


def _snap_to_table_start(md: str, pos: int) -> int:
    """Move *pos* back to the start of the markdown table containing it.

    A content anchor can land on a row in the middle of a table, which would
    drop the rows above it from every section.  Snap to the table's first row,
    and include a one-cell caption row directly above it (a statement title).
    Positions that are not inside a table, or are already on its first row,
    are returned unchanged.
    """
    line_start = _line_start(md, pos)
    if not md.startswith("|", line_start):
        return pos
    start = line_start
    while start > 0:
        prev_start = _line_start(md, start - 1)
        if md.startswith("|", prev_start):
            start = prev_start
        else:
            break
    if start == line_start:
        return pos
    if md[max(start - 2, 0):start] == "\n\n":
        prev_start = _line_start(md, start - 2)
        if _ONE_CELL_ROW.fullmatch(md[prev_start:start - 2]):
            start = prev_start
    return start


def _snap_anchor(md: str, pos: int) -> int:
    """Move a content anchor to the start of its line (or its table).

    ``md.find`` can return a position after a ``# `` or ``**`` prefix, which
    would leave the prefix dangling at the end of the previous section.
    """
    pos = _snap_to_table_start(md, pos)
    line_start = _line_start(md, pos)
    if _LINE_PREFIX_MARKUP.fullmatch(md[line_start:pos]):
        return line_start
    return pos


def _is_page_number_row(md: str, pos: int) -> bool:
    """True when the line at *pos* ends in a bare page-number cell (a ToC row)."""
    line_start = _line_start(md, pos)
    line_end = md.find("\n", pos)
    line = md[line_start:line_end if line_end != -1 else len(md)]
    return bool(_PAGE_NUMBER_CELL.search(line))


def _find_content_anchor(
    md: str, sec_text: str, md_len: int, min_pos: int | None = None
) -> int | None:
    """Find where *sec_text* content begins in *md* using the first unique line.

    Searches the body (from 2 % to 99.5 % of *md*) for the first line of
    *sec_text* of at least 15 characters that appears at most 5 times.

    *min_pos* overrides the default 2 % lower bound (used for signatures).
    """
    body_min = int(md_len * _BODY_MIN_FRAC)
    max_pos = int(md_len * _BODY_MAX_FRAC)
    start = max(min_pos, body_min) if min_pos is not None else body_min

    for line in (ln.strip() for ln in sec_text.splitlines()):
        if len(line) < _ANCHOR_MIN_LINE_CHARS:
            continue
        count = md.count(line, start, max_pos)
        if 0 < count <= _ANCHOR_MAX_OCCURRENCES:
            pos = md.find(line, start, max_pos)
            if pos != -1:
                return pos
    return None


def _parse_cross_reference_index(md: str) -> dict[str, str]:
    """Parse the cross-reference index from the last 12 % of *md*.

    Returns ``{item_num_lower: section_title}`` e.g. ``{"7": "Management's
    Discussion and Analysis of Financial Condition and Results of Operations"}``.
    """
    tail_start = int(len(md) * _CROSS_REF_TAIL_FRAC)
    tail = md[tail_start:]

    pat = re.compile(
        r"^\| *item +(\w+)\.? *\| *([^|\n]+?) *\|",
        re.IGNORECASE | re.MULTILINE,
    )
    result: dict[str, str] = {}
    for m in pat.finditer(tail):
        num = m.group(1).lower()
        title = m.group(2).strip().rstrip(":")
        if num not in result:  # first occurrence wins (Part I before Part II)
            result[num] = title
    return result


def _find_title_in_body(md: str, title: str, min_pos: int, max_pos: int) -> int | None:
    """Find start of the section titled *title* in md[min_pos:max_pos].

    Only body headings count; table-of-contents and index rows are skipped:

    1. A one-cell table row starting with the first 35 chars of *title*.  Rows
       with a page number or an empty trailing cell are not one-cell rows.
    2. A standalone heading containing the first 3 significant words of *title*.
    3. A bare, short line beginning with the title.

    The first tier with any hit wins; within a tier the earliest hit wins.
    """
    phrase = title[:_TITLE_PHRASE_CHARS].rstrip()
    search = md[min_pos:max_pos]

    pat_cell = re.compile(
        rf"^\| *{re.escape(phrase)}[^|\n]*\| *$",
        re.IGNORECASE | re.MULTILINE,
    )
    words = [w for w in re.split(r"\W+", title) if len(w) >= _MIN_TITLE_WORD_CHARS]
    patterns: list[re.Pattern[str]] = [pat_cell]
    if len(words) >= 2:
        anchor = r"[^\w\n]+".join(re.escape(w) for w in words[:3])
        patterns.append(re.compile(rf"^#+ +{anchor}", re.IGNORECASE | re.MULTILINE))
    bare_line = rf"^{re.escape(phrase)}[^|\n]{{0,{_BARE_LINE_MAX_EXTRA}}}$"
    patterns.append(re.compile(bare_line, re.IGNORECASE | re.MULTILINE))
    # Tiers in order of trust; the first tier with a hit wins, so a sub-heading
    # that repeats the title cannot beat the section's own title row.
    for pat in patterns:
        m = pat.search(search)
        if m:
            return min_pos + m.start()
    return None


def _find_statements_caption(md: str, min_pos: int, max_pos: int) -> int | None:
    """Start of the first primary-statement caption (income statement) in the body.

    Last-resort locator for a filing's financial-statements section when it has
    no item heading and no usable cross-reference title.  Index and ToC rows
    carry a page-number cell, so they do not match.
    """
    m = _STATEMENTS_CAPTION.search(md[min_pos:max_pos])
    return min_pos + m.start() if m else None


def _heading_skeleton(
    md: str, order: list[str], min_pos: int, max_pos: int
) -> dict[str, int]:
    """Sequentially locate item headings in canonical order.

    The floor advances after each hit so that item numbers repeated across
    Parts (10-Q Part I Item 2 vs Part II Item 2) land on different headings.
    A hit in the cross-reference tail does not advance it: a stray index row
    must not hide the headings of the keys that follow.
    """
    found: dict[str, int] = {}
    floor = min_pos
    tail_start = int(len(md) * _CROSS_REF_TAIL_FRAC)
    for key in order:
        pos = _find_heading_pos(md, key, floor, max_pos)
        if pos is not None:
            found[key] = pos
            if pos < tail_start:
                floor = pos + 1
    return found


def _anchor_agrees(md: str, anchor: int, reference: int | None) -> bool:
    """Whether an edgartools content anchor can be trusted.

    With an independent reference (heading or cross-reference title) the
    anchor must land on it.  Without one, it must begin a line (a heading, not
    a fragment of a sentence) and must not sit on a ToC row.
    """
    if reference is not None:
        return abs(anchor - reference) <= ANCHOR_TOLERANCE
    prefix = md[_line_start(md, anchor):anchor]
    return _LINE_PREFIX_MARKUP.fullmatch(prefix) is not None and not _is_page_number_row(
        md, anchor
    )


_INDEX_ITEM_ROW = re.compile(
    r"^\| *item +(\d+[a-c]?)\.? *\| *[^|\n]+? *\|", re.IGNORECASE | re.MULTILINE
)
_INDEX_SUB_ROW = re.compile(r"^\| *\| *([^|\n]+?) *\|", re.MULTILINE)


def _parse_index_subtitles(md: str) -> dict[str, list[str]]:
    """Sub-topics the filer's cross-reference index lists under each item.

    Intel's index has rows such as ``|  | Critical accounting estimates | Pages
    34-36 |`` directly under ``| Item 7. | ... |``.  Returns
    ``{item_num_lower: [sub_title, ...]}``; the first occurrence of an item wins.
    """
    tail = md[int(len(md) * _CROSS_REF_TAIL_FRAC):]
    subtitles: dict[str, list[str]] = {}
    current: str | None = None
    for line in tail.splitlines():
        item = _INDEX_ITEM_ROW.match(line)
        if item:
            num = item.group(1).lower()
            if num in subtitles:
                current = None  # a second Part's rows must not extend the first
            else:
                subtitles[num] = []
                current = num
            continue
        sub = _INDEX_SUB_ROW.match(line)
        if sub and current is not None:
            subtitles[current].append(sub.group(1).strip())
    return subtitles


def _continuation_starts(
    md: str,
    positions: dict[str, tuple[int, str]],
    required: frozenset[str],
    subtitles: dict[str, list[str]],
    max_pos: int,
) -> list[tuple[str, int]]:
    """Starts of required items that continue after another item's section.

    A sub-topic the index lists under a required item counts when its title is
    found after the item's contiguous block ends and before the next located
    required section.  Intel prints Critical Accounting Estimates (Item 7) after
    Item 7A.  Deliberately conservative:

    * no later required section located: no search (the window would be open-ended);
    * item numbers shared by two Parts (10-Q Item 1, 2, ...) are skipped, because
      the index map is keyed by number only;
    * generic or very short titles ("None", "Not applicable") are ignored.
    """
    ordered = sorted((pos, key) for key, (pos, _) in positions.items())
    used = {pos for pos, _ in ordered}
    nums = [_item_num_from_key(key) for key in positions]
    starts: list[tuple[str, int]] = []
    for key in sorted(required & positions.keys()):
        num = _item_num_from_key(key) or ""
        titles = [t for t in subtitles.get(num, []) if _is_specific_title(t)]
        later = [(pos, other) for pos, other in ordered if pos > positions[key][0]]
        next_required = next((pos for pos, other in later if other in required), None)
        if not titles or not later or next_required is None or nums.count(num) > 1:
            continue
        for title in titles:
            pos = _find_title_in_body(md, title, later[0][0], min(next_required, max_pos))
            if pos is not None and pos not in used:
                starts.append((key, pos))
                used.add(pos)
    return starts


def _is_specific_title(title: str) -> bool:
    """False for index sub-rows too generic to locate in the body."""
    return (
        len(title) >= _MIN_CONTINUATION_TITLE_CHARS
        and title.lower().rstrip(".") not in _GENERIC_INDEX_TITLES
    )


def _locate_reference(
    md: str,
    key: str,
    headings: dict[str, int],
    cross_ref: dict[str, str],
    required: frozenset[str],
    max_pos: int,
) -> tuple[int | None, str]:
    """Independent position for *key*: heading, else (required keys) title or caption."""
    if key in headings:
        return headings[key], "heading"
    if key not in required:
        return None, "heading"
    title = cross_ref.get((_item_num_from_key(key) or "").lower())
    if title:
        pos = _find_title_in_body(md, title, 0, max_pos)
        if pos is not None:
            return pos, "cross_reference_index"
    if key in _FINANCIAL_STATEMENT_KEYS:
        return _find_statements_caption(md, 0, max_pos), "heading"
    return None, "heading"


def _locate_anchor(md: str, doc: object, key: str) -> int | None:
    """Where edgartools' text for *key* begins in *md*, snapped to its line or table."""
    sec_text = (doc.get_sec_section(key, clean=True) or "").strip()  # type: ignore[attr-defined]
    if not sec_text:
        return None
    anchor_min = int(len(md) * _SIGNATURES_MIN_FRAC) if key == "part_iv_signatures" else None
    raw = _find_content_anchor(md, sec_text, len(md), min_pos=anchor_min)
    return _snap_anchor(md, raw) if raw is not None else None


def _dedupe_positions(
    entries: list[tuple[str, int, str]], order: list[str]
) -> list[tuple[str, int, str]]:
    """Resolve keys sharing a position (keep the canonically-earlier) and sort by position."""
    seen: dict[int, tuple[str, str]] = {}
    for key, pos, method in entries:
        if pos not in seen or _sort_key(key, order) < _sort_key(seen[pos][0], order):
            seen[pos] = (key, method)
    return sorted(
        [(key, pos, method) for pos, (key, method) in seen.items()],
        key=lambda item: item[1],
    )


def _choose_start(
    md: str, anchor: int | None, ref_pos: int | None, ref_method: str
) -> tuple[int, str] | None:
    """Pick between an edgartools anchor and the independent reference, or neither."""
    if anchor is not None and _anchor_agrees(md, anchor, ref_pos):
        # With a reference, start at the heading itself rather than at the
        # first unique content line after it.
        return (anchor if ref_pos is None else ref_pos), "edgartools"
    if ref_pos is not None:
        return ref_pos, ref_method
    return None


def _build_section_positions(
    md: str, form_type: str, doc: object
) -> tuple[list[tuple[str, int, str]], frozenset[str]]:
    """Return ``([(key, start_pos_in_md, method), ...] sorted by position, edgartools keys)``.

    Headings and cross-reference titles are precise but not always present;
    edgartools content anchors are always available but sometimes wrong (they
    can land in the exhibit list, a ToC row, or mid-table).  For each canonical
    key the independent reference is the heading, else, for required keys, the
    cross-reference title (or, for the financial statements, the first
    primary-statement caption).  Then:

    * an edgartools anchor that agrees with the reference (or has none and is
      not a ToC row) is used and labelled ``edgartools``;
    * otherwise the reference is used and labelled by where it came from:
      ``heading`` or ``cross_reference_index``.

    A required item the index lists sub-topics for can have further spans
    (``_continuation_starts``).  The second return value is the set of keys
    edgartools itself identified, whether or not its anchor was used; its keys
    outside the canonical order are ignored.
    """
    is_10k = "10-K" in form_type
    order = SECTION_ORDER_10K if is_10k else SECTION_ORDER_10Q
    required = REQUIRED_SECTIONS_10K if is_10k else REQUIRED_SECTIONS_10Q
    identified = frozenset(doc.get_available_sec_sections())  # type: ignore[attr-defined]
    max_pos = int(len(md) * _BODY_MAX_FRAC)

    # Heading and title patterns reject ToC rows by shape (page-number or empty
    # trailing cell), so they need no body floor: AMD's body starts at 1.7 %.
    headings = _heading_skeleton(md, order, 0, max_pos)
    cross_ref = _parse_cross_reference_index(md)

    positions: dict[str, tuple[int, str]] = {}
    for key in order:
        ref_pos, ref_method = _locate_reference(md, key, headings, cross_ref, required, max_pos)
        anchor = _locate_anchor(md, doc, key) if key in identified else None
        chosen = _choose_start(md, anchor, ref_pos, ref_method)
        if chosen is not None:
            positions[key] = chosen

    continuations = _continuation_starts(
        md, positions, required, _parse_index_subtitles(md), max_pos
    )
    entries = [(key, pos, method) for key, (pos, method) in positions.items()]
    entries += [(key, pos, "continuation") for key, pos in continuations]
    return _dedupe_positions(entries, order), identified


# ── Public API ────────────────────────────────────────────────────────────────

def parse_filing(html: str, meta: FilingMeta) -> ParsedFiling:
    """Parse raw EDGAR HTML and return an immutable ParsedFiling.

    See :func:`parse_filing_with_methods` for the per-section extraction method.
    """
    return parse_filing_with_methods(html, meta)[0]


def _assemble_sections(
    md: str,
    found: list[tuple[str, int, str]],
    identified: frozenset[str],
) -> tuple[str, list[ParsedSection], list[str], dict[str, str]]:
    """Slice *md* at the located positions and build offsets by construction.

    Returns ``(text, sections, fallback_keys, methods)``.  ``text`` is the
    section texts joined by ``_SEPARATOR``; each section's offsets are recorded
    as it is appended, so ``text[start:end]`` is exact without any search.
    Text before the first located section becomes a ``preamble`` section, so
    the whole filing is covered by offsets.  A key may own several spans; its
    method is that of its first span.
    """
    if not found or found[0][1] > 0:
        found = [(PREAMBLE_KEY, 0, PREAMBLE_KEY), *found]
    cursor = 0
    sections: list[ParsedSection] = []
    parts: list[str] = []
    fallback_keys: list[str] = []
    methods: dict[str, str] = {}

    for i, (key, md_start, method) in enumerate(found):
        md_end = found[i + 1][1] if i + 1 < len(found) else len(md)
        sec_text = md[md_start:md_end].rstrip()
        if not sec_text.strip():
            continue

        char_end = cursor + len(sec_text)
        sections.append(ParsedSection(label=key, char_start=cursor, char_end=char_end))
        parts.append(sec_text)
        cursor = char_end + len(_SEPARATOR)
        methods.setdefault(key, method)
        # A fallback is a section edgartools did not locate itself: a key it does
        # not know, or one placed through the cross-reference index.
        is_fallback = method == "cross_reference_index" or (
            method == "heading" and key not in identified
        )
        if is_fallback and key not in fallback_keys:
            fallback_keys.append(key)

    return _SEPARATOR.join(parts), sections, fallback_keys, methods


def _convert_tables(doc: object) -> list[ParsedTable]:
    """Every edgartools table, rendered exactly as edgartools writes it into the markdown.

    A table with no content columns renders as ``""`` and is kept, so the list
    matches edgartools' own table count.  ``_render_table`` is edgartools'
    per-table renderer; it has no public equivalent in 5.59 (pinned below 6.0),
    and tests/test_parser.py checks the renderings appear verbatim in the text.
    """
    renderer = MarkdownRenderer()
    raw_tables: list[object] = getattr(doc, "tables", [])
    return [
        ParsedTable(index=i, markdown=renderer._render_table(tbl).strip())  # type: ignore[arg-type]
        for i, tbl in enumerate(raw_tables)
    ]


def parse_filing_with_methods(
    html: str, meta: FilingMeta
) -> tuple[ParsedFiling, dict[str, str]]:
    """Parse raw EDGAR HTML; also return ``{section_key: extraction_method}``.

    Methods are ``edgartools`` (its content anchor was used), ``heading``,
    ``cross_reference_index``, ``preamble`` for the leading text, or ``continuation``
    for a later span of an item; a key's method is that of its first span.
    They cover exactly the labels present in ``ParsedFiling.sections``.
    ``fallback_sections`` lists the sections edgartools did not locate itself;
    a key it identified but a heading positioned is labelled ``heading`` yet is
    not a fallback.

    Offsets are exact by construction, not search.  Tables are embedded inline
    in the section text; ``ingest.tables`` locates them there.
    """
    doc = parse_html(html)
    md: str = doc.to_markdown()  # type: ignore[attr-defined]
    if len(md) < 500:
        raise ValueError(
            f"to_markdown() returned suspiciously short output ({len(md)} chars) "
            f"for {meta.accession_no}. Possibly a parse failure."
        )

    found, identified = _build_section_positions(md, meta.form_type, doc)
    text, sections, fallback_keys, methods = _assemble_sections(md, found, identified)

    # Built from the parsed sections (not from `found`) so a section skipped for
    # an empty slice cannot create a false "all present" signal.
    required = REQUIRED_SECTIONS_10K if "10-K" in meta.form_type else REQUIRED_SECTIONS_10Q
    missing = sorted(required - {s.label for s in sections})
    for sec in missing:
        logger.warning(
            "Required section '%s' missing from %s (%s)",
            sec, meta.accession_no, meta.form_type,
        )

    filing = ParsedFiling(
        accession_no=meta.accession_no,
        cik=meta.cik,
        form_type=meta.form_type,
        fiscal_period=meta.fiscal_period,
        text=text,
        sections=sections,
        tables=_convert_tables(doc),
        missing_sections=missing,
        fallback_sections=fallback_keys,
    )
    return filing, methods
