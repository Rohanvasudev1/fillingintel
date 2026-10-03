"""Tests for the section-aware chunker (Step 3b)."""
import pytest

from ingest.chunker import Chunk, chunk_filing, sentence_starts
from ingest.models import ParsedFiling, ParsedSection
from tests.chunk_checks import (
    MAX_OVERLAP_TOKENS,
    MAX_TOKENS,
    assert_all_chunk_checks,
    assert_sections_covered,
    tokens,
)

FIXTURE_FILINGS = [
    "nvda_10k_filing",
    "nvda_10q_filing",
    "amd_10k_filing",
    "amd_10q_filing",
    "intc_10k_filing",
    "intc_10q_filing",
]
ACCESSION = "0000000000-26-000001"


def _filing(text: str, bounds: list[tuple[str, int, int]] | None = None) -> ParsedFiling:
    bounds = bounds or [("part_i_item_1", 0, len(text))]
    return ParsedFiling(
        accession_no=ACCESSION,
        cik="1",
        form_type="10-Q",
        fiscal_period="FY2026-Q1",
        text=text,
        sections=[ParsedSection(label=lbl, char_start=s, char_end=e) for lbl, s, e in bounds],
    )


def _paragraph(tag: str, n_sentences: int) -> str:
    """Prose of roughly 12 tokens per sentence, with edgartools-style escaped periods."""
    return " ".join(
        f"Revenue for segment {tag} grew in quarter number {i} of the year\\."
        for i in range(n_sentences)
    )


def _table(n_rows: int) -> str:
    rows = "\n".join(f"| Line item {i} | {i},000 | {i},500 |" for i in range(n_rows))
    return f"| Item | 2026 | 2025 |\n| --- | --- | --- |\n{rows}"


def _texts(filing: ParsedFiling, chunks: tuple[Chunk, ...]) -> list[str]:
    return [filing.text[c.char_start : c.char_end] for c in chunks]


# ── Small inputs ──────────────────────────────────────────────────────────────

class TestSmallInputs:
    def test_short_section_is_one_chunk_with_filing_metadata(self):
        filing = _filing("Item 1B. Unresolved Staff Comments\n\nNone\\.")
        (chunk,) = chunk_filing(filing)
        assert filing.text[chunk.char_start : chunk.char_end] == filing.text
        assert chunk.chunk_id == f"{ACCESSION}:0000"
        assert (chunk.section, chunk.ordinal, chunk.contains_table) == ("part_i_item_1", 0, False)
        assert_all_chunk_checks(filing, (chunk,))

    def test_surrounding_whitespace_is_not_in_the_chunk(self):
        filing = _filing("\n\n  Some text here\\.  \n\n")
        (chunk,) = chunk_filing(filing)
        assert filing.text[chunk.char_start : chunk.char_end] == "Some text here\\."

    def test_whitespace_only_section_gives_no_chunk(self):
        body = "Body text\\."
        text = f"{body}\n\n   "
        filing = _filing(
            text, [("part_i_item_1", 0, len(body)), ("part_i_item_2", len(body) + 2, len(text))]
        )
        assert [c.section for c in chunk_filing(filing)] == ["part_i_item_1"]

    def test_chunk_model_rejects_end_before_start(self):
        with pytest.raises(ValueError):
            Chunk(
                chunk_id=f"{ACCESSION}:0000", cik="1", accession_no=ACCESSION, form_type="10-Q",
                fiscal_period="FY2026-Q1", section="x", char_start=5, char_end=5, ordinal=0,
                token_count=1, tokenizer="cl100k_base", chunker_version="1", contains_table=False,
            )


# ── Packing and overlap ───────────────────────────────────────────────────────

class TestPacking:
    def test_paragraphs_pack_up_to_the_limit_then_overlap(self):
        paras = [_paragraph(t, 25) for t in "ABCD"]  # ~300 tokens each
        filing = _filing("\n\n".join(paras))
        chunks = chunk_filing(filing)
        assert len(chunks) >= 2
        assert all(c.token_count <= MAX_TOKENS for c in chunks)
        assert chunks[1].char_start < chunks[0].char_end, "consecutive chunks overlap"
        assert_all_chunk_checks(filing, chunks)

    def test_overlap_starts_at_a_sentence_start(self):
        filing = _filing("\n\n".join(_paragraph(t, 25) for t in "ABCD"))
        chunks = chunk_filing(filing)
        for c in chunks[1:]:
            before = filing.text[: c.char_start]
            assert before.endswith("\\. ") or before.endswith("\n\n"), repr(before[-10:])

    def test_overlap_is_at_most_100_tokens(self):
        filing = _filing("\n\n".join(_paragraph(t, 25) for t in "ABCDEF"))
        chunks = chunk_filing(filing)
        for prev, nxt in zip(chunks, chunks[1:]):
            assert tokens(filing.text[nxt.char_start : prev.char_end]) <= MAX_OVERLAP_TOKENS

    def test_long_paragraph_is_split_at_sentence_ends(self):
        filing = _filing(_paragraph("A", 150))  # ~1,800 tokens, no blank lines
        chunks = chunk_filing(filing)
        assert len(chunks) >= 3
        assert all(c.token_count <= MAX_TOKENS for c in chunks)
        for c in chunks:
            assert filing.text[c.char_start : c.char_end].endswith("\\.")
        assert_all_chunk_checks(filing, chunks)

    def test_tab_separated_words_stay_within_the_limit(self):
        filing = _filing("\t\t".join(f"word{i}" for i in range(1500)))
        chunks = chunk_filing(filing)
        assert all(c.token_count <= MAX_TOKENS for c in chunks)
        assert_all_chunk_checks(filing, chunks)

    def test_one_word_longer_than_the_limit_is_cut_by_characters(self):
        filing = _filing("Prefix sentence\\. " + "x7Q" * 4000)  # one ~4,000-token "word"
        chunks = chunk_filing(filing)
        assert len(chunks) >= 5
        assert all(c.token_count <= MAX_TOKENS for c in chunks)
        assert_sections_covered(filing, chunks)

    def test_sentence_ends_before_a_closing_paren_and_after_abbreviations(self):
        text = "Revenue rose \\(see Note 2\\.) Costs fell\\. Sales in the U.S. grew\\."
        starts = sentence_starts(text, 0, len(text))
        assert starts[:3] == [0, text.index("Costs"), text.index("Sales")]

    def test_sentence_longer_than_the_limit_is_split_at_whitespace(self):
        filing = _filing(" ".join(f"word{i}" for i in range(1500)))  # no sentence end
        chunks = chunk_filing(filing)
        assert all(c.token_count <= MAX_TOKENS for c in chunks)
        for c in chunks:
            body = filing.text[c.char_start : c.char_end]
            assert body == body.strip() and body.startswith("word")
        assert_all_chunk_checks(filing, chunks)


# ── Tables ────────────────────────────────────────────────────────────────────

class TestTables:
    def test_small_table_packs_with_its_caption(self):
        text = f"Inventories consisted of the following\\.\n\n{_table(5)}\n\nMore text\\."
        filing = _filing(text)
        (chunk,) = chunk_filing(filing)
        assert chunk.contains_table

    def test_oversized_table_is_one_chunk_with_its_caption(self):
        caption = "The following table shows revenue by line item\\."
        table = _table(120)  # well over 800 tokens
        filing = _filing("\n\n".join([_paragraph("A", 50), caption, table, "After the table\\."]))
        chunks = chunk_filing(filing)
        (holder,) = [c for c in chunks if c.contains_table]
        body = filing.text[holder.char_start : holder.char_end]
        assert table in body
        assert holder.token_count > MAX_TOKENS
        assert caption in body, "overlap before a table carries its caption"
        assert_all_chunk_checks(filing, chunks)

    def test_chunk_after_a_table_starts_at_the_table_end_or_later(self):
        table = _table(120)
        filing = _filing("\n\n".join([_paragraph("A", 10), table, _paragraph("B", 80)]))
        chunks = chunk_filing(filing)
        table_end = filing.text.index(table) + len(table)
        after = [c for c in chunks if c.char_end > table_end and not c.contains_table]
        assert after and all(c.char_start >= table_end for c in after)
        assert_all_chunk_checks(filing, chunks)

    def test_glued_prose_stays_inside_the_table_chunk(self):
        # NVDA 10-K shape: a table row runs into a sentence on the same line.
        table = "| Header | x |\n| Income before income tax | $ | 141,450 | The income tax differs"
        filing = _filing(f"{table}\nfrom the statutory rate as follows:\n\n{_table(3)}")
        chunks = chunk_filing(filing)
        glued = filing.text.index("The income tax differs")
        holders = [c for c in chunks if c.char_start <= glued < c.char_end]
        assert holders and all(c.contains_table for c in holders)
        assert_all_chunk_checks(filing, chunks)


# ── Sections ──────────────────────────────────────────────────────────────────

class TestSections:
    def test_chunks_never_join_two_sections(self):
        a, b = _paragraph("A", 5), _paragraph("B", 5)
        text = f"{a}\n\n{b}"
        filing = _filing(
            text, [("part_i_item_1", 0, len(a)), ("part_i_item_2", len(a) + 2, len(text))]
        )
        chunks = chunk_filing(filing)
        assert [c.section for c in chunks] == ["part_i_item_1", "part_i_item_2"]
        assert_all_chunk_checks(filing, chunks)

    def test_spans_of_one_item_are_chunked_separately_with_the_same_label(self):
        a, mid, b = _paragraph("A", 5), _paragraph("M", 5), _paragraph("B", 5)
        text = f"{a}\n\n{mid}\n\n{b}"
        s2, s3 = len(a) + 2, len(a) + 2 + len(mid) + 2
        filing = _filing(
            text,
            [("part_ii_item_7", 0, len(a)), ("part_ii_item_7a", s2, s2 + len(mid)),
             ("part_ii_item_7", s3, len(text))],
        )
        chunks = chunk_filing(filing)
        labels = [c.section for c in chunks]
        assert labels == ["part_ii_item_7", "part_ii_item_7a", "part_ii_item_7"]

    def test_preamble_is_chunked(self):
        text = "Cover page text\\.\n\nItem 1 body\\."
        filing = _filing(text, [("preamble", 0, 17), ("part_i_item_1", 19, len(text))])
        assert [c.section for c in chunk_filing(filing)] == ["preamble", "part_i_item_1"]

    def test_chunking_is_deterministic(self):
        filing = _filing("\n\n".join(_paragraph(t, 25) for t in "ABCD"))
        assert chunk_filing(filing) == chunk_filing(filing)


# ── Acceptance checks on the six real fixture filings ─────────────────────────

@pytest.mark.parametrize("name", FIXTURE_FILINGS)
def test_fixture_filing_passes_all_chunk_checks(name, request):
    filing = request.getfixturevalue(name)
    assert_all_chunk_checks(filing, chunk_filing(filing))


def test_nvda_10k_fiscal_summary_table_is_whole_in_one_chunk(nvda_10k_filing):
    text = nvda_10k_filing.text
    start = text.index("|  | Jan 25, 2026 |  |  | Jan 26, 2025 |")
    end = text.index("| Net income per diluted share | $ | 4.90 |", start)
    chunks = chunk_filing(nvda_10k_filing)
    holders = [c for c in chunks if c.char_start <= start and end < c.char_end]
    assert len(holders) == 1 and holders[0].section == "part_ii_item_7"


def test_chunking_a_real_filing_twice_gives_the_same_chunks(amd_10q_filing):
    assert chunk_filing(amd_10q_filing) == chunk_filing(amd_10q_filing)


def test_intc_10q_largest_table_is_an_oversized_table_chunk(intc_10q_filing):
    chunks = chunk_filing(intc_10q_filing)
    biggest = max(chunks, key=lambda c: c.token_count)
    assert biggest.contains_table and biggest.token_count > MAX_TOKENS
