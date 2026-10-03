"""Chunker acceptance checks shared by the fixture tests and the local corpus tests.

Token counts are recomputed here with tiktoken directly, not through the chunker.
"""
import tiktoken

from ingest.chunker import Chunk
from ingest.models import ParsedFiling
from ingest.tables import find_table_spans

ENCODING = tiktoken.get_encoding("cl100k_base")
MAX_TOKENS = 800
MAX_OVERLAP_TOKENS = 100


def tokens(text: str) -> int:
    return len(ENCODING.encode(text))


def _section_index(filing: ParsedFiling, chunk: Chunk) -> int:
    holders = [
        i
        for i, s in enumerate(filing.sections)
        if s.char_start <= chunk.char_start and chunk.char_end <= s.char_end
    ]
    assert len(holders) == 1, f"{chunk.chunk_id} lies in sections {holders}, not exactly one"
    return holders[0]


def assert_chunk_fields(filing: ParsedFiling, chunks: tuple[Chunk, ...]) -> None:
    assert chunks, f"{filing.accession_no}: no chunks"
    for c in chunks:
        assert (c.cik, c.accession_no, c.form_type, c.fiscal_period) == (
            filing.cik,
            filing.accession_no,
            filing.form_type,
            filing.fiscal_period,
        )
        assert c.chunk_id == f"{filing.accession_no}:{c.ordinal:04d}"
        assert c.token_count == tokens(filing.text[c.char_start : c.char_end])
        assert (c.tokenizer, c.chunker_version) == ("cl100k_base", "1")


def assert_ids_unique_and_ordered(chunks: tuple[Chunk, ...]) -> None:
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    starts = [c.char_start for c in chunks]
    assert all(a < b for a, b in zip(starts, starts[1:])), "chunk starts strictly increase"


def assert_chunks_stay_in_one_section(filing: ParsedFiling, chunks: tuple[Chunk, ...]) -> None:
    for c in chunks:
        section = filing.sections[_section_index(filing, c)]
        assert c.section == section.label, f"{c.chunk_id}: {c.section} != {section.label}"


def assert_tables_not_split(filing: ParsedFiling, chunks: tuple[Chunk, ...]) -> None:
    tables = find_table_spans(filing)
    for t in tables:
        holders = [c for c in chunks if c.char_start <= t.char_start and t.char_end <= c.char_end]
        assert holders, f"{filing.accession_no}: table at {t.char_start} is in no single chunk"
    for c in chunks:
        holds = any(c.char_start <= t.char_start and t.char_end <= c.char_end for t in tables)
        assert c.contains_table == holds, f"{c.chunk_id}: contains_table={c.contains_table}"


def assert_chunks_start_at_whitespace(filing: ParsedFiling, chunks: tuple[Chunk, ...]) -> None:
    """A chunk starts at a section start or after whitespace (a sentence, paragraph or word)."""
    section_starts = {s.char_start for s in filing.sections}
    for c in chunks:
        if c.char_start in section_starts:
            continue
        context = filing.text[c.char_start - 20 : c.char_start + 20]
        assert filing.text[c.char_start - 1].isspace(), f"{c.chunk_id} mid-word: {context!r}"


def assert_only_table_chunks_oversized(chunks: tuple[Chunk, ...]) -> None:
    for c in chunks:
        if c.token_count > MAX_TOKENS:
            assert c.contains_table, f"{c.chunk_id}: {c.token_count} tokens and no table"


def assert_sections_covered(filing: ParsedFiling, chunks: tuple[Chunk, ...]) -> None:
    """Every non-whitespace character of every section is in at least one chunk."""
    covered = bytearray(len(filing.text))
    for c in chunks:
        covered[c.char_start : c.char_end] = b"\x01" * (c.char_end - c.char_start)
    for s in filing.sections:
        for pos in range(s.char_start, s.char_end):
            if not covered[pos] and not filing.text[pos].isspace():
                raise AssertionError(
                    f"{filing.accession_no}/{s.label}: char {pos} in no chunk: "
                    f"{filing.text[pos : pos + 60]!r}"
                )


def assert_overlap_bounded_and_not_from_tables(
    filing: ParsedFiling, chunks: tuple[Chunk, ...]
) -> None:
    tables = find_table_spans(filing)
    for prev, nxt in zip(chunks, chunks[1:]):
        if nxt.char_start >= prev.char_end:
            continue
        overlap = filing.text[nxt.char_start : prev.char_end]
        assert tokens(overlap) <= MAX_OVERLAP_TOKENS, f"{nxt.chunk_id}: overlap too long"
        for t in tables:
            assert t.char_end <= nxt.char_start or t.char_start >= prev.char_end, (
                f"{nxt.chunk_id}: overlap includes the table at {t.char_start}"
            )


def assert_all_chunk_checks(filing: ParsedFiling, chunks: tuple[Chunk, ...]) -> None:
    assert_chunk_fields(filing, chunks)
    assert_ids_unique_and_ordered(chunks)
    assert_chunks_stay_in_one_section(filing, chunks)
    assert_tables_not_split(filing, chunks)
    assert_chunks_start_at_whitespace(filing, chunks)
    assert_only_table_chunks_oversized(chunks)
    assert_sections_covered(filing, chunks)
    assert_overlap_bounded_and_not_from_tables(filing, chunks)
