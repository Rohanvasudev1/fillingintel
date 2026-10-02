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

# An edgartools content anchor is trusted over a heading or cross-reference
# title only when it lands within this many characters of it.
ANCHOR_TOLERANCE = 400
_BODY_MAX_FRAC = 0.995
_CROSS_REF_TAIL_FRAC = 0.88  # the filer's item cross-reference index sits in this tail
_SIGNATURES_MIN_FRAC = 0.80  # signatures are always in the last 20 % of a filing
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


def _table_to_markdown(table: object) -> str:
    """Convert an edgartools table node to a pipe-delimited markdown string."""
    rows = list(table.rows) if hasattr(table, "rows") else []  # type: ignore[attr-defined]
    if not rows:
        return str(table)
    lines: list[str] = []
    for row in rows:
        cells = list(row.cells) if hasattr(row, "cells") else []
        cell_texts: list[str] = [
            (getattr(cell, "content", None) or "")
            .replace("|", "\\|")
            .replace("\n", " ")
            .strip()
            for cell in cells
        ]
        lines.append("| " + " | ".join(cell_texts) + " |")
    return "\n".join(lines)


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

    Searches in md[min_pos:max_pos] for the first line of *sec_text* that
    appears ≤ 5 times in the body (2–99.5 % of md).

    *min_pos* overrides the default 2 % lower bound (used for signatures).
    """
    body_min = int(md_len * 0.02)
    max_pos = int(md_len * 0.995)
    start = max(min_pos, body_min) if min_pos is not None else body_min

    for line in (ln.strip() for ln in sec_text.splitlines()):
        if len(line) < 15:
            continue
        count = md.count(line, start, max_pos)
        if 0 < count <= 5:
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
    positions: dict[str, tuple[int, str]], order: list[str]
) -> list[tuple[str, int, str]]:
    """Resolve keys sharing a position (keep the canonically-earlier) and sort by position."""
    seen: dict[int, tuple[str, str]] = {}
    for key, (pos, method) in positions.items():
        if pos not in seen or _sort_key(key, order) < _sort_key(seen[pos][0], order):
            seen[pos] = (key, method)
    return sorted(
        [(key, pos, method) for pos, (key, method) in seen.items()],
        key=lambda item: item[1],
    )


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

    The second return value is the set of keys edgartools itself identified,
    whether or not its anchor was used.  Keys edgartools reports that are not
    in the canonical order are ignored: their text stays inside the preceding
    canonical section.
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
        if anchor is not None and _anchor_agrees(md, anchor, ref_pos):
            # With a reference, start at the heading itself rather than at the
            # first unique content line after it.
            positions[key] = (anchor if ref_pos is None else ref_pos, "edgartools")
        elif ref_pos is not None:
            positions[key] = (ref_pos, ref_method)

    return _dedupe_positions(positions, order), identified


# ── Public API ────────────────────────────────────────────────────────────────

def parse_filing(html: str, meta: FilingMeta) -> ParsedFiling:
    """Parse raw EDGAR HTML and return an immutable ParsedFiling.

    See :func:`parse_filing_with_methods` for the per-section extraction method.
    """
    return parse_filing_with_methods(html, meta)[0]


def parse_filing_with_methods(
    html: str, meta: FilingMeta
) -> tuple[ParsedFiling, dict[str, str]]:
    """Parse raw EDGAR HTML; also return ``{section_key: extraction_method}``.

    Methods are ``edgartools`` (its content anchor was used), ``heading`` or
    ``cross_reference_index``, and cover exactly the sections present in
    ``ParsedFiling.sections``.  ``fallback_sections`` lists the sections edgartools
    did not locate itself; a key it identified but a heading positioned is
    labelled ``heading`` yet is not a fallback.

    ``result.text[sec.char_start : sec.char_end] == sec_text`` for every
    section — guaranteed by construction, not search.

    Tables are embedded inline in section texts via ``doc.to_markdown()``.
    ``ParsedTable.section_key`` is ``None`` in Step 2b; assigned in Step 3.
    """
    doc = parse_html(html)
    md: str = doc.to_markdown()  # type: ignore[attr-defined]
    if len(md) < 500:
        raise ValueError(
            f"to_markdown() returned suspiciously short output ({len(md)} chars) "
            f"for {meta.accession_no}. Possibly a parse failure."
        )

    required = REQUIRED_SECTIONS_10K if "10-K" in meta.form_type else REQUIRED_SECTIONS_10Q

    found, identified = _build_section_positions(md, meta.form_type, doc)

    # ── Construction-based offsets ────────────────────────────────────────────
    cursor = 0
    parsed_sections: list[ParsedSection] = []
    text_parts: list[str] = []
    fallback_keys: list[str] = []
    methods: dict[str, str] = {}

    for i, (key, md_start, method) in enumerate(found):
        md_end = found[i + 1][1] if i + 1 < len(found) else len(md)
        sec_text = md[md_start:md_end].rstrip()
        if not sec_text.strip():
            continue

        char_start = cursor
        char_end = cursor + len(sec_text)
        cursor = char_end + len(_SEPARATOR)

        parsed_sections.append(
            ParsedSection(label=key, char_start=char_start, char_end=char_end)
        )
        text_parts.append(sec_text)
        methods[key] = method
        # A fallback is a section edgartools did not locate itself: a key it does
        # not know, or one placed through the cross-reference index.
        if method == "cross_reference_index" or (method == "heading" and key not in identified):
            fallback_keys.append(key)

    full_text = _SEPARATOR.join(text_parts)

    # ── Missing required sections ─────────────────────────────────────────────
    # Build from the actual parsed_sections list (not from `found`) so that
    # sections with empty text slices — which are skipped above — do not create
    # a false "all present" signal in the returned object.
    parsed_labels = {s.label for s in parsed_sections}
    missing = sorted(required - parsed_labels)
    for sec in missing:
        logger.warning(
            "Required section '%s' missing from %s (%s)",
            sec, meta.accession_no, meta.form_type,
        )

    # ── Tables (flat list; section_key=None until Step 3) ────────────────────
    raw_tables: list[object] = getattr(doc, "tables", [])
    parsed_tables: list[ParsedTable] = []
    for i, tbl in enumerate(raw_tables):
        tbl_md = _table_to_markdown(tbl)
        if tbl_md.strip():
            parsed_tables.append(ParsedTable(index=i, markdown=tbl_md, section_key=None))

    filing = ParsedFiling(
        accession_no=meta.accession_no,
        cik=meta.cik,
        form_type=meta.form_type,
        fiscal_period=meta.fiscal_period,
        text=full_text,
        sections=parsed_sections,
        tables=parsed_tables,
        missing_sections=missing,
        fallback_sections=fallback_keys,
    )
    return filing, methods
