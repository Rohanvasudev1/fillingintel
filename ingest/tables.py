"""Table spans: tables located by their position in a filing's parsed text (Step 3a).

edgartools renders each table into the markdown as rows of ``|`` cells, so the
parsed text already holds every table in place.  A table span is a run of
consecutive rows; a blank line ends it.  A row starts at a line beginning with
``|`` and ends at the first line that ends with ``|``, because a cell can
contain line breaks (Intel prints "Exhibit\\n\\n\\nNumber").  A row that does not
close within ``MAX_ROW_LINES`` lines is an edgartools rendering glitch (a row
running into a prose paragraph); it ends at its own line and is counted.
The mirror glitch also occurs: a table's header row printed at the end of the
paragraph before it, so the table's own lines start at the ``| --- |``
separator.  The span then starts where the header row begins in that line.

The text is edgartools markdown, which uses LF line endings only.

Spans are found inside each section, so a span never crosses a section
boundary.  ``tables_crossing_sections`` scans the whole text to check that no
section boundary cuts a table in two.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from ingest.models import ParsedFiling

ROW_MARK = "|"
MAX_ROW_LINES = 20  # longest closed row in the 30 cached filings is 10 lines
SEPARATOR_ROW = re.compile(r"\|(?: *:?-{3,}:? *\|)+ *")
_HEADER_AND_SEPARATOR_ROWS = 2  # a glued-header block starts with these two rows
_Count = Annotated[int, Field(ge=0)]


class TableSpan(BaseModel):
    """One table in a filing's parsed text, with exact character offsets."""

    model_config = {"frozen": True}

    char_start: Annotated[int, Field(ge=0)]
    char_end: int
    section: str  # label of the containing section
    section_index: _Count  # index into ParsedFiling.sections (labels can repeat)
    rows: Annotated[int, Field(ge=1)]
    unclosed_rows: _Count  # rows that never reached a closing "|"
    header_glued: bool = False  # header row printed at the end of the preceding prose line

    @field_validator("char_end")
    @classmethod
    def end_after_start(cls, v: int, info: ValidationInfo) -> int:
        start = info.data.get("char_start")
        if start is not None and v <= start:
            raise ValueError(f"char_end ({v}) must be greater than char_start ({start})")
        return v


@dataclass(frozen=True)
class _Block:
    start: int
    end: int
    rows: int
    unclosed: int
    glued: bool = False


def _line_end(text: str, pos: int, hi: int) -> int:
    end = text.find("\n", pos, hi)
    return hi if end == -1 else end


def _row_end(text: str, pos: int, hi: int) -> tuple[int, bool]:
    """End offset of the row starting at *pos*, and whether it closed with ``|``.

    A row is unclosed if it reaches another row (a line starting with ``|``),
    the end of the section or ``MAX_ROW_LINES`` lines before it closes; it then
    ends at its own first line.
    """
    first_end = _line_end(text, pos, hi)
    end = first_end
    for _ in range(MAX_ROW_LINES):
        if text[pos:end].rstrip().endswith(ROW_MARK):
            return end, True
        if end + 1 >= hi or text.startswith(ROW_MARK, end + 1, hi):
            break
        end = _line_end(text, end + 1, hi)
    return first_end, False


def _glued_header_start(text: str, pos: int, sep_end: int, lo: int) -> int | None:
    """Start of a header row glued to the end of the prose line before *pos*.

    Only called when the row at ``text[pos:sep_end]`` is a separator row, which
    must follow a header.  The header has as many cells as the separator, so it
    starts at the n-th ``|`` from the end of the line, where n is the
    separator's pipe count.  An earlier ``|`` in the prose is not part of it.
    """
    if pos <= lo or text[pos - 1] != "\n":
        return None
    line_start = max(lo, text.rfind("\n", lo, pos - 1) + 1)
    line_end = line_start + len(text[line_start : pos - 1].rstrip())
    if line_end <= line_start or text[line_end - 1] != ROW_MARK:
        return None
    start = line_end
    for _ in range(text.count(ROW_MARK, pos, sep_end)):
        start = text.rfind(ROW_MARK, line_start, start)
        if start == -1:
            return None
    has_prose_before = start > line_start
    return start if has_prose_before else None


def _new_block(text: str, pos: int, end: int, lo: int, unclosed: int) -> _Block:
    """A block starting with the row at *pos*, pulled back over a glued header row."""
    glued = None
    if SEPARATOR_ROW.fullmatch(text, pos, end):
        glued = _glued_header_start(text, pos, end, lo)
    if glued is None:
        return _Block(pos, end, 1, unclosed)
    return _Block(glued, end, _HEADER_AND_SEPARATOR_ROWS, unclosed, glued=True)


def _scan_blocks(text: str, lo: int, hi: int) -> list[_Block]:
    """Runs of consecutive table rows within ``text[lo:hi]``."""
    blocks: list[_Block] = []
    current: _Block | None = None
    pos = lo
    while pos < hi:
        if not text.startswith(ROW_MARK, pos):
            if current is not None:
                blocks.append(current)
                current = None
            pos = _line_end(text, pos, hi) + 1
            continue
        end, closed = _row_end(text, pos, hi)
        unclosed = 0 if closed else 1
        if current is None:
            current = _new_block(text, pos, end, lo, unclosed)
        else:
            current = _Block(
                current.start, end, current.rows + 1, current.unclosed + unclosed, current.glued
            )
        pos = end + 1
    if current is not None:
        blocks.append(current)
    return blocks


def find_table_spans(filing: ParsedFiling) -> tuple[TableSpan, ...]:
    """Every table in *filing*, found section by section, in document order."""
    spans: list[TableSpan] = []
    for index, section in enumerate(filing.sections):
        for block in _scan_blocks(filing.text, section.char_start, section.char_end):
            spans.append(
                TableSpan(
                    char_start=block.start,
                    char_end=block.end,
                    section=section.label,
                    section_index=index,
                    rows=block.rows,
                    unclosed_rows=block.unclosed,
                    header_glued=block.glued,
                )
            )
    return tuple(sorted(spans, key=lambda s: s.char_start))


def tables_crossing_sections(filing: ParsedFiling) -> tuple[tuple[int, int], ...]:
    """``(start, end)`` of each table in the whole text that no single section contains."""
    bounds = [(s.char_start, s.char_end) for s in filing.sections]
    return tuple(
        (b.start, b.end)
        for b in _scan_blocks(filing.text, 0, len(filing.text))
        if not any(lo <= b.start and b.end <= hi for lo, hi in bounds)
    )



class TableReconciliation(BaseModel):
    """How edgartools' table list lines up with the table spans in the text.

    The counts satisfy ``span_count == edgartools_tables - rendered_empty
    - sharing_a_span - not_in_text - outside_spans + spans_without_table``.
    ``not_in_text`` and ``outside_spans`` should be 0 on real filings.
    """

    model_config = {"frozen": True}

    edgartools_tables: _Count
    rendered_empty: _Count  # tables edgartools writes no text for (no content columns)
    sharing_a_span: _Count  # tables printed straight after another, with no blank line
    spans_without_table: _Count  # spans no edgartools table starts in (e.g. one-cell titles)
    not_in_text: _Count  # renderings not found verbatim in the text
    outside_spans: _Count  # renderings found, but starting outside every span
    span_count: _Count


def _span_index_at(spans: tuple[TableSpan, ...], starts: list[int], pos: int) -> int | None:
    k = bisect_right(starts, pos) - 1
    return k if k >= 0 and pos < spans[k].char_end else None


def reconcile_tables(filing: ParsedFiling) -> TableReconciliation:
    """Locate each edgartools table rendering in the text, in order, and count the outcomes."""
    spans = find_table_spans(filing)
    starts = [s.char_start for s in spans]
    hits: list[int] = []
    empty = not_in_text = outside = 0
    pos = 0
    for table in filing.tables:
        if not table.markdown:
            empty += 1
            continue
        found = filing.text.find(table.markdown, pos)
        if found == -1:
            not_in_text += 1
            continue
        pos = found + 1
        k = _span_index_at(spans, starts, found)
        if k is None:
            outside += 1
        else:
            hits.append(k)
    distinct = len(set(hits))
    return TableReconciliation(
        edgartools_tables=len(filing.tables),
        rendered_empty=empty,
        sharing_a_span=len(hits) - distinct,
        spans_without_table=len(spans) - distinct,
        not_in_text=not_in_text,
        outside_spans=outside,
        span_count=len(spans),
    )
