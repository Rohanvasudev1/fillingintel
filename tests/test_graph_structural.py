"""read_structural(): filings, chunks with offsets and risk-factor sections (Steps 10–11).

Runs against a throwaway Postgres schema holding the NVIDIA 10-K and 10-Q
fixtures; skips without DATABASE_URL.
"""
import pytest

from graph.build_input import RISK_SECTION_BY_FORM
from graph.structural import read_structural
from ingest.chunker import chunk_filing
from ingest.store import load_filing
from tests.conftest import throwaway_schema

NAMES = ("nvda_10k", "nvda_10q")


@pytest.fixture(scope="module")
def loaded(fixture_records):
    records = [r for r in fixture_records
               if r.meta.accession_no in {"0001045810-26-000021", "0001045810-26-000075"}]
    assert len(records) == len(NAMES)
    with throwaway_schema() as conn:
        for record in records:
            load_filing(conn, record, chunk_filing(record.filing))
        conn.commit()
        yield conn, records


def test_chunks_carry_the_chunkers_offsets(loaded):
    conn, records = loaded
    rows = read_structural(conn)
    found = {c.chunk_id: (c.char_start, c.char_end) for c in rows.chunks}
    for record in records:
        for chunk in chunk_filing(record.filing):
            assert found[chunk.chunk_id] == (chunk.char_start, chunk.char_end)


def test_each_filing_has_its_risk_section_from_first_to_last_chunk(loaded):
    conn, records = loaded
    sections = {s.accession_no: s for s in read_structural(conn).risk_sections}
    assert set(sections) == {r.meta.accession_no for r in records}
    for record in records:
        name = RISK_SECTION_BY_FORM[record.meta.form_type]
        risk = [c for c in chunk_filing(record.filing) if c.section == name]
        start, end = min(c.char_start for c in risk), max(c.char_end for c in risk)
        section = sections[record.meta.accession_no]
        assert section.char_start == start
        assert section.text == record.filing.text[start:end]
