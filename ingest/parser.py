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

    a. **edgartools** – key is in ``get_available_sec_sections()``.
       Positioned via heading scan; if that fails, via content anchor on
       the first unique line of ``get_sec_section(key, clean=True)``.
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
    1. Standalone markdown heading: ``# Item N[A-Z]?. ...``
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
        rf"^#+ +item +{num_pat}[.\s]",
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


def _find_content_anchor(
    md: str, sec_text: str, md_len: int, min_pos: int | None = None
) -> int | None:
    """Find where *sec_text* content begins in *md* using the first unique line.

    Searches in md[min_pos:max_pos] for the first line of *sec_text* that
    appears ≤ 5 times in the body (2–99.5 % of md).

    *min_pos* overrides the default 2 % lower bound to enable sequential
    processing: each found anchor can advance the floor for the next search.
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
    tail_start = int(len(md) * 0.88)
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

    Tries:
    1. Single-cell table row starting with first 35 chars of *title*.
    2. Standalone heading containing the first 3 significant words of *title*.
    """
    phrase = title[:35].rstrip()
    search = md[min_pos:max_pos]
    candidates: list[int] = []

    # Single-cell prefix match
    pat_cell = re.compile(
        rf"^\| *{re.escape(phrase)}",
        re.IGNORECASE | re.MULTILINE,
    )
    m1 = pat_cell.search(search)
    if m1:
        candidates.append(min_pos + m1.start())

    # Standalone heading with first 3 significant words
    words = [w for w in re.split(r"\W+", title) if len(w) > 3]
    if len(words) >= 2:
        anchor = r"[\s\W]+".join(re.escape(w) for w in words[:3])
        pat_head = re.compile(
            rf"^#+ +{anchor}",
            re.IGNORECASE | re.MULTILINE,
        )
        m2 = pat_head.search(search)
        if m2:
            candidates.append(min_pos + m2.start())

    return min(candidates) if candidates else None


def _build_section_positions(
    md: str, form_type: str, doc: object
) -> list[tuple[str, int, str]]:
    """Return ``[(key, start_pos_in_md, extraction_method), ...]`` sorted by position.

    Three-step fallback chain:

    1. **Content anchor** for every edgartools-available key — searches the
       full body for the first unique line of the section text.  This is the
       most reliable locator because it uses actual content, not heading format.

    2. **Sequential heading scan** for keys not yet placed — searches only the
       document body (2–90 %) to avoid picking up cross-reference table rows
       at the end.  Sequential processing with an advancing ``next_min``
       prevents duplicate item numbers (e.g. "2" in part_i_item_2 and
       part_ii_item_2) from colliding on the same heading occurrence.

    3. **Cross-reference index** for required keys still missing — parses the
       last 12 % for ``| item N. | title |`` entries and finds the title in the
       document body.  First occurrence wins per item number so that Part I
       titles take priority over same-numbered Part II titles.
    """
    order = SECTION_ORDER_10K if "10-K" in form_type else SECTION_ORDER_10Q
    required = REQUIRED_SECTIONS_10K if "10-K" in form_type else REQUIRED_SECTIONS_10Q
    available_keys: list[str] = doc.get_available_sec_sections()  # type: ignore[attr-defined]
    available_set = set(available_keys)

    md_len = len(md)
    min_body = int(md_len * 0.02)
    # Heading scan covers body only (2–90 %) to avoid cross-reference rows.
    max_body = int(md_len * 0.90)
    max_pos = int(md_len * 0.995)

    positions: dict[str, tuple[int, str]] = {}

    # Canonical order + any extra keys edgartools found, sorted by canonical index
    all_keys_ordered: list[str] = sorted(
        set(order) | available_set,
        key=lambda k: _sort_key(k, order),
    )

    # ── Step 1: content anchor for edgartools-available keys ─────────────────
    # Non-sequential — edgartools sections may not follow canonical document
    # order (e.g. INTC's content anchors are scattered across the filing).
    for key in all_keys_ordered:
        if key not in available_set:
            continue
        sec_text = (doc.get_sec_section(key, clean=True) or "").strip()  # type: ignore[attr-defined]
        if not sec_text:
            continue
        # Signatures always appear in the last 20 % of a filing; restrict the
        # search to avoid matching executive-officer text earlier in the doc.
        anchor_min = int(md_len * 0.80) if key == "part_iv_signatures" else None
        pos = _find_content_anchor(md, sec_text, md_len, min_pos=anchor_min)
        if pos is not None:
            positions[key] = (pos, "edgartools")

    # ── Step 2: sequential heading scan for keys not yet placed ──────────────
    # Body-only (2–90 %) to avoid matching cross-reference table rows that
    # repeat "| Item N. |" patterns near the end of the filing.
    next_min = min_body
    for key in all_keys_ordered:
        if key in positions:
            # Only advance next_min if the already-placed position is within the
            # heading search range (2–90 %).  Positions set by content anchor
            # may be beyond max_body (e.g. AMD part_i_item_2 at 98 %); advancing
            # next_min past max_body would make earlier body headings unreachable.
            placed_pos = positions[key][0]
            if placed_pos < max_body:
                next_min = max(next_min, placed_pos + 1)
            continue
        pos = _find_heading_pos(md, key, next_min, max_body)
        if pos is not None:
            method = "edgartools" if key in available_set else "heading"
            positions[key] = (pos, method)
            next_min = pos + 1

    # ── Step 3: cross-reference index (required keys still missing) ───────────
    cross_ref = _parse_cross_reference_index(md)
    for key in required:
        if key in positions:
            continue
        num = _item_num_from_key(key)
        if num is None:
            continue
        title = cross_ref.get(num.lower())
        if not title:
            continue
        pos = _find_title_in_body(md, title, min_body, max_pos)
        if pos is not None:
            positions[key] = (pos, "cross_reference_index")

    # Deduplicate: when two keys land on the same position (edgartools may map
    # multiple keys to identical content), keep the canonically-earlier one so
    # the required section always gets the text slice.
    seen_positions: dict[int, tuple[str, str]] = {}
    for k, (p, meth) in positions.items():
        if p not in seen_positions:
            seen_positions[p] = (k, meth)
        else:
            existing_key = seen_positions[p][0]
            # Prefer the key that ranks earlier in canonical order
            if _sort_key(k, order) < _sort_key(existing_key, order):
                seen_positions[p] = (k, meth)

    found = sorted(
        [(k, p, meth) for p, (k, meth) in seen_positions.items()],
        key=lambda x: x[1],
    )
    return found


# ── Public API ────────────────────────────────────────────────────────────────

def parse_filing(html: str, meta: FilingMeta) -> ParsedFiling:
    """Parse raw EDGAR HTML and return an immutable ParsedFiling.

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

    found = _build_section_positions(md, meta.form_type, doc)

    # ── Construction-based offsets ────────────────────────────────────────────
    cursor = 0
    parsed_sections: list[ParsedSection] = []
    text_parts: list[str] = []
    fallback_keys: list[str] = []

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
        if method != "edgartools":
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

    return ParsedFiling(
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
