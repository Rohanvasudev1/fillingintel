"""Section-aware chunker (Step 3b).

Each section span of a ``ParsedFiling`` is cut into units, and units are
packed greedily into chunks of at most ``MAX_CHUNK_TOKENS``:

- A table span (``ingest.tables``) is one unit and is never split, even when it
  is over the limit.  Prose that edgartools glued onto a table row stays in it.
- Text between tables splits into paragraphs at blank lines.  A paragraph over
  the limit splits at sentence ends, and a sentence over the limit splits into
  runs of whole words.

A new chunk starts up to ``OVERLAP_TOKENS`` back, at a sentence or unit start
in the text before it.  Overlap never reaches back past a table, so a chunk
that follows a table starts at the table's end.  A text chunk's overlap
shrinks to keep the chunk within the limit; a table chunk may take its full
overlap (usually the table's caption) even though that makes it larger.

No chunk crosses a section span.  Offsets are exact: a chunk's text is
``filing.text[char_start:char_end]``, trimmed of surrounding whitespace.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Annotated

import tiktoken
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from ingest.models import ParsedFiling, ParsedSection
from ingest.tables import TableSpan, find_table_spans

MAX_CHUNK_TOKENS = 800
OVERLAP_TOKENS = 100
TOKENIZER = "cl100k_base"
CHUNKER_VERSION = "1"

_ENCODING = tiktoken.get_encoding(TOKENIZER)
_BLANK_LINE = re.compile(r"\n[ \t]*\n")
_SENTENCE_BREAK = re.compile(r"[.?!][\"')\]]*\s+(?=\S)")
_WORD = re.compile(r"\S+")
_Count = Annotated[int, Field(ge=0)]


class Chunk(BaseModel):
    """One chunk of a filing, with exact character offsets into its parsed text."""

    model_config = {"frozen": True}

    chunk_id: str  # "{accession_no}:{ordinal:04d}"
    cik: str
    accession_no: str
    form_type: str
    fiscal_period: str
    section: str
    char_start: _Count
    char_end: int
    ordinal: _Count  # position in the filing, in document order
    token_count: _Count
    tokenizer: str
    chunker_version: str
    contains_table: bool

    @field_validator("char_end")
    @classmethod
    def end_after_start(cls, v: int, info: ValidationInfo) -> int:
        start = info.data.get("char_start")
        if start is not None and v <= start:
            raise ValueError(f"char_end ({v}) must be greater than char_start ({start})")
        return v


@dataclass(frozen=True)
class _Unit:
    start: int
    end: int
    is_table: bool = False


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    contains_table: bool


def count_tokens(text: str) -> int:
    """Number of cl100k_base tokens in *text*."""
    return len(_ENCODING.encode(text))


# ── Units ─────────────────────────────────────────────────────────────────────

def _trimmed(text: str, start: int, end: int) -> _Unit | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return _Unit(start, end) if end > start else None


def _paragraphs(text: str, start: int, end: int) -> Iterator[_Unit]:
    """Blank-line-separated paragraphs in ``text[start:end]``, trimmed."""
    pos = start
    for m in _BLANK_LINE.finditer(text, start, end):
        unit = _trimmed(text, pos, m.start())
        if unit:
            yield unit
        pos = m.end()
    unit = _trimmed(text, pos, end)
    if unit:
        yield unit


def sentence_starts(text: str, start: int, end: int) -> list[int]:
    """*start* and every sentence start in ``text[start:end]``.

    A sentence ends at ``.``, ``?`` or ``!`` (edgartools escapes periods as
    ``\\.``), optionally followed by closing quotes or brackets, then whitespace.
    Abbreviations such as "U.S." over-split; that only adds overlap candidates.
    """
    return [start] + [m.end() for m in _SENTENCE_BREAK.finditer(text, start, end)]


def _last_fitting(text: str, start: int, cuts: Sequence[int]) -> int | None:
    """The largest cut with ``text[start:cut]`` within the limit (binary search)."""
    lo, hi, best = 0, len(cuts) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if count_tokens(text[start : cuts[mid]]) <= MAX_CHUNK_TOKENS:
            best, lo = cuts[mid], mid + 1
        else:
            hi = mid - 1
    return best


def _word_runs(text: str, unit: _Unit) -> Iterator[_Unit]:
    """Runs of whole words, each within the limit by exact count.

    A single word over the limit (a long URL or hash) is cut by characters.
    """
    word_ends = [m.end() for m in _WORD.finditer(text, unit.start, unit.end)]
    pos = unit.start
    while pos < unit.end:
        ends = word_ends[bisect_right(word_ends, pos) :]
        if not ends:
            return
        cut = _last_fitting(text, pos, ends)
        if cut is None:
            cut = _last_fitting(text, pos, range(pos + 1, ends[0] + 1)) or pos + 1
        run = _trimmed(text, pos, cut)
        if run:
            yield run
        pos = cut
        while pos < unit.end and text[pos].isspace():
            pos += 1


def _split_long(text: str, unit: _Unit) -> Iterator[_Unit]:
    """A text unit over the limit, as sentences, and over-long sentences as word runs."""
    starts = sentence_starts(text, unit.start, unit.end)
    for s, e in zip(starts, starts[1:] + [unit.end]):
        sentence = _trimmed(text, s, e)
        if sentence is None:
            continue
        if count_tokens(text[sentence.start : sentence.end]) <= MAX_CHUNK_TOKENS:
            yield sentence
        else:
            yield from _word_runs(text, sentence)


def _units(text: str, section: ParsedSection, tables: list[TableSpan]) -> list[_Unit]:
    """Table and text units of one section span, in order."""
    units: list[_Unit] = []
    pos = section.char_start
    for table in [*tables, None]:
        gap_end = table.char_start if table else section.char_end
        for para in _paragraphs(text, pos, gap_end):
            if count_tokens(text[para.start : para.end]) <= MAX_CHUNK_TOKENS:
                units.append(para)
            else:
                units.extend(_split_long(text, para))
        if table:
            units.append(_Unit(table.char_start, table.char_end, is_table=True))
            pos = table.char_end
    return units


# ── Packing ───────────────────────────────────────────────────────────────────

def _first_true(items: Sequence[int], ok: Callable[[int], bool]) -> int | None:
    """The first item for which *ok* holds, where *ok* is false then true (binary search)."""
    lo, hi, found = 0, len(items) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if ok(items[mid]):
            found, hi = items[mid], mid - 1
        else:
            lo = mid + 1
    return found


def _overlap_start(text: str, units: list[_Unit], i: int, prev: _Span) -> int:
    """Earliest start for the chunk opening at ``units[i]`` that keeps the overlap rules.

    Candidates are sentence starts after ``prev.start`` in the text units before
    ``units[i]``, back to the nearest table.  Both limits only loosen as the
    start moves later, so the earliest valid start is found by binary search.
    """
    nxt = units[i]
    region: list[_Unit] = []
    for u in reversed(units[:i]):
        if u.is_table or u.end <= prev.start:
            break
        region.append(u)
    candidates = sorted(
        s
        for u in region
        for s in sentence_starts(text, u.start, u.end)
        if s > prev.start
    )

    def fits(s: int) -> bool:
        if count_tokens(text[s : prev.end]) > OVERLAP_TOKENS:
            return False
        return nxt.is_table or count_tokens(text[s : nxt.end]) <= MAX_CHUNK_TOKENS

    found = _first_true(candidates, fits)
    return nxt.start if found is None else found


def _pack(text: str, units: list[_Unit]) -> list[_Span]:
    spans: list[_Span] = []
    current: _Span | None = None
    for i, unit in enumerate(units):
        if current is None:
            current = _Span(unit.start, unit.end, unit.is_table)
        elif count_tokens(text[current.start : unit.end]) <= MAX_CHUNK_TOKENS:
            current = _Span(current.start, unit.end, current.contains_table or unit.is_table)
        else:
            spans.append(current)
            current = _Span(_overlap_start(text, units, i, current), unit.end, unit.is_table)
    if current is not None:
        spans.append(current)
    return spans


def chunk_filing(filing: ParsedFiling) -> tuple[Chunk, ...]:
    """Every chunk of *filing*, section span by section span, in document order."""
    tables = find_table_spans(filing)
    order = sorted(range(len(filing.sections)), key=lambda i: filing.sections[i].char_start)
    chunks: list[Chunk] = []
    for index in order:
        section = filing.sections[index]
        section_tables = [t for t in tables if t.section_index == index]
        for span in _pack(filing.text, _units(filing.text, section, section_tables)):
            chunks.append(_make_chunk(filing, section.label, span, len(chunks)))
    return tuple(chunks)


def _make_chunk(filing: ParsedFiling, label: str, span: _Span, ordinal: int) -> Chunk:
    return Chunk(
        chunk_id=f"{filing.accession_no}:{ordinal:04d}",
        cik=filing.cik,
        accession_no=filing.accession_no,
        form_type=filing.form_type,
        fiscal_period=filing.fiscal_period,
        section=label,
        char_start=span.start,
        char_end=span.end,
        ordinal=ordinal,
        token_count=count_tokens(filing.text[span.start : span.end]),
        tokenizer=TOKENIZER,
        chunker_version=CHUNKER_VERSION,
        contains_table=span.contains_table,
    )
