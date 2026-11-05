"""A cited chunk's source passage in its filing context (Step 14 ticket 01).

Against a throwaway Postgres schema holding two real fixture filings; the
chunk text must be exactly what ``resolve()`` returns.  Needs ``DATABASE_URL``.
"""
import pytest

from ingest.chunker import chunk_filing
from ingest.store import load_filing, resolve
from tests.conftest import FIXTURE_NAMES, throwaway_schema
from ui.source import CONTEXT_CHARS, SourceNotFound, edgar_index_url, read_source

LOADED = ("nvda_10k", "amd_10q")


@pytest.fixture(scope="module")
def loaded(fixture_records):
    with throwaway_schema() as conn:
        chunked = {}
        for name in LOADED:
            record = fixture_records[FIXTURE_NAMES.index(name)]
            chunked[name] = (record, chunk_filing(record.filing))
            load_filing(conn, record, chunked[name][1])
        yield conn, chunked


def test_the_passage_is_the_resolved_chunk_inside_its_filing_text(loaded):
    conn, chunked = loaded
    record, chunks = chunked["nvda_10k"]
    chunk = chunks[40]
    passage = read_source(conn, chunk.chunk_id)
    text = record.filing.text
    assert passage.chunk == resolve(conn, chunk.chunk_id)
    assert (passage.char_start, passage.char_end) == (chunk.char_start, chunk.char_end)
    assert passage.before == text[chunk.char_start - CONTEXT_CHARS:chunk.char_start]
    assert passage.after == text[chunk.char_end:chunk.char_end + CONTEXT_CHARS]
    assert passage.context_start == chunk.char_start - CONTEXT_CHARS


def test_the_passage_carries_the_filings_metadata(loaded):
    conn, chunked = loaded
    record, chunks = chunked["amd_10q"]
    passage = read_source(conn, chunks[3].chunk_id)
    meta = record.meta
    assert (passage.ticker, passage.cik, passage.accession_no) == (
        "AMD", meta.cik, meta.accession_no)
    assert passage.form_type == "10-Q"
    assert passage.fiscal_period == meta.fiscal_period
    assert passage.section == chunks[3].section
    assert passage.edgar_url == edgar_index_url(meta.cik, meta.accession_no)


def test_context_stops_at_the_start_and_end_of_the_filing(loaded):
    conn, chunked = loaded
    record, chunks = chunked["nvda_10k"]
    first = read_source(conn, chunks[0].chunk_id)
    assert first.context_start == 0
    assert first.before == record.filing.text[:chunks[0].char_start]
    last = read_source(conn, chunks[-1].chunk_id)
    assert last.after == record.filing.text[chunks[-1].char_end:]


def test_an_unknown_chunk_is_refused_by_id(loaded):
    conn, _ = loaded
    with pytest.raises(SourceNotFound, match="0001045810-26-000021:9999"):
        read_source(conn, "0001045810-26-000021:9999")


def test_the_edgar_link_is_built_from_cik_and_accession_without_a_fetch():
    assert edgar_index_url("1045810", "0001045810-26-000021") == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/"
        "0001045810-26-000021-index.htm")
    assert edgar_index_url("0000002488", "0000002488-25-000047").startswith(
        "https://www.sec.gov/Archives/edgar/data/2488/")
