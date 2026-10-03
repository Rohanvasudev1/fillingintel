"""Tests for the Postgres store: schema, loading and resolve() (Step 3c).

These run against ``DATABASE_URL`` in a throwaway schema (see ``db_conn`` in
conftest.py).  CI provides Postgres; locally run ``docker compose up -d``.
"""
import hashlib
import os
import random

import psycopg
import pytest
from psycopg import sql

from ingest.chunker import chunk_filing, make_chunk_id
from ingest.load import SAMPLE_SEED, SAMPLE_SIZE
from ingest.store import (
    ChunkNotFound,
    SchemaMismatch,
    apply_schema,
    financial_statements_section,
    get_chunk,
    load_filing,
    resolve,
    stored_offsets,
)


def test_ci_always_has_a_database():
    """The database tests skip without DATABASE_URL; CI must never skip them."""
    if os.environ.get("CI"):
        assert os.environ.get("DATABASE_URL"), "CI must set DATABASE_URL"


@pytest.fixture(scope="module")
def loaded(db_conn, fixture_records):
    """All six fixture filings loaded; returns ``{accession_no: (record, chunks)}``."""
    out = {}
    for record in fixture_records:
        chunks = chunk_filing(record.filing)
        load_filing(db_conn, record, chunks)
        out[record.meta.accession_no] = (record, chunks)
    return out


def _count(conn, table: str, accession_no: str) -> int:
    query = sql.SQL("SELECT count(*) FROM {} WHERE accession_no = %s").format(sql.Identifier(table))
    return conn.execute(query, (accession_no,)).fetchone()[0]


# ── Schema ────────────────────────────────────────────────────────────────────

def test_schema_applies_twice(db_conn):
    apply_schema(db_conn)
    apply_schema(db_conn)


def test_chunk_for_an_unknown_filing_is_rejected(db_conn, loaded):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db_conn.execute(
            "INSERT INTO chunks (chunk_id, accession_no, cik, form_type, fiscal_period, section,"
            " char_start, char_end, ordinal, token_count, tokenizer, chunker_version,"
            " contains_table) VALUES ('9999999999-99-999999:0000', '9999999999-99-999999', '1',"
            " '10-K', 'FY1', 's', 0, 1, 0, 1, 'cl100k_base', '1', false)"
        )
    db_conn.rollback()


def test_chunk_id_that_disagrees_with_its_ordinal_is_rejected_by_the_database(db_conn, loaded):
    accession_no = next(iter(loaded))
    with pytest.raises(psycopg.errors.CheckViolation):
        db_conn.execute(
            "INSERT INTO chunks (chunk_id, accession_no, cik, form_type, fiscal_period, section,"
            " char_start, char_end, ordinal, token_count, tokenizer, chunker_version,"
            " contains_table) VALUES (%s, %s, '1', '10-K', 'FY1', 's', 0, 5, 9998, 1,"
            " 'cl100k_base', '1', false)",
            (f"{accession_no}:0001", accession_no),
        )
    db_conn.rollback()


def test_filing_whose_hash_does_not_match_its_text_is_rejected(db_conn, loaded):
    accession_no = next(iter(loaded))
    with pytest.raises(psycopg.errors.CheckViolation):
        db_conn.execute(
            "UPDATE filings SET text_sha256 = repeat('0', 64) WHERE accession_no = %s",
            (accession_no,),
        )
    db_conn.rollback()


def test_apply_schema_fails_loudly_when_live_columns_drift(db_conn, loaded):
    db_conn.execute("ALTER TABLE chunks ADD COLUMN stray integer")
    db_conn.commit()
    try:
        with pytest.raises(SchemaMismatch, match="stray"):
            apply_schema(db_conn)
    finally:
        db_conn.rollback()
        db_conn.execute("ALTER TABLE chunks DROP COLUMN stray")
        db_conn.commit()


_INSERT_CHUNK = (
    "INSERT INTO chunks (chunk_id, accession_no, cik, form_type, fiscal_period, section,"
    " char_start, char_end, ordinal, token_count, tokenizer, chunker_version, contains_table)"
    " VALUES (%s, %s, '1', %s, 'FY1', 's', %s, %s, %s, 1, 'cl100k_base', '1', false)"
)


def _insert_chunk(conn, accession_no, *, form_type="10-K", start=0, end=5, ordinal=9999):
    """Insert one chunk row directly; every field is valid unless overridden."""
    chunk_id = make_chunk_id(accession_no, ordinal)
    conn.execute(_INSERT_CHUNK, (chunk_id, accession_no, form_type, start, end, ordinal))


@pytest.mark.parametrize(
    "override",
    [
        {"start": 5, "end": 4},  # end before start
        {"start": 5, "end": 5},  # empty span
        {"start": -1, "end": 5},  # negative start
        {"form_type": "8-K"},  # not a 10-K or 10-Q
    ],
    ids=["end-before-start", "empty-span", "negative-start", "bad-form-type"],
)
def test_invalid_chunk_row_is_rejected_by_a_check(db_conn, loaded, override):
    accession_no = next(iter(loaded))
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_chunk(db_conn, accession_no, **override)
    db_conn.rollback()


def test_valid_direct_insert_is_accepted(db_conn, loaded):
    """Control for the CHECK tests: the same row with no override goes in."""
    accession_no = next(iter(loaded))
    _insert_chunk(db_conn, accession_no)
    db_conn.rollback()


# ── Loading ───────────────────────────────────────────────────────────────────

def test_every_chunk_is_stored(db_conn, loaded):
    for accession_no, (_, chunks) in loaded.items():
        assert _count(db_conn, "chunks", accession_no) == len(chunks)


def test_filing_row_holds_text_hash_and_commit(db_conn, loaded):
    for accession_no, (record, _) in loaded.items():
        text, sha, commit = db_conn.execute(
            "SELECT parsed_text, text_sha256, parser_commit FROM filings WHERE accession_no = %s",
            (accession_no,),
        ).fetchone()
        assert text == record.filing.text
        assert sha == hashlib.sha256(record.filing.text.encode("utf-8")).hexdigest()
        assert commit == "test"


def test_stored_chunk_matches_the_chunker(db_conn, loaded):
    record, chunks = next(iter(loaded.values()))
    for chunk in chunks[:5]:
        assert get_chunk(db_conn, chunk.chunk_id) == chunk


def test_reload_is_idempotent(db_conn, loaded):
    record, chunks = next(iter(loaded.values()))
    load_filing(db_conn, record, chunks)
    assert _count(db_conn, "chunks", record.meta.accession_no) == len(chunks)
    assert _count(db_conn, "filings", record.meta.accession_no) == 1


def test_reload_with_fewer_chunks_removes_the_old_ones(db_conn, loaded):
    record, chunks = next(iter(loaded.values()))
    try:
        load_filing(db_conn, record, chunks[:3])
        assert _count(db_conn, "chunks", record.meta.accession_no) == 3
    finally:
        load_filing(db_conn, record, chunks)  # restore for the other tests
    assert _count(db_conn, "chunks", record.meta.accession_no) == len(chunks)


def test_failed_load_leaves_the_previous_rows_untouched(db_conn, loaded):
    record, chunks = next(iter(loaded.values()))
    duplicate = (*chunks, chunks[0])  # second insert of the same chunk_id fails
    with pytest.raises(psycopg.errors.UniqueViolation):
        load_filing(db_conn, record, duplicate)
    db_conn.rollback()
    assert _count(db_conn, "chunks", record.meta.accession_no) == len(chunks)
    assert resolve(db_conn, chunks[0].chunk_id) == record.filing.text[
        chunks[0].char_start : chunks[0].char_end
    ]


def test_chunks_from_another_filing_are_refused(db_conn, loaded):
    (rec_a, chunks_a), (_, chunks_b) = list(loaded.values())[:2]
    with pytest.raises(ValueError, match="another filing"):
        load_filing(db_conn, rec_a, (*chunks_a, chunks_b[0]))


def test_chunk_past_the_end_of_the_text_is_refused(db_conn, loaded):
    record, chunks = next(iter(loaded.values()))
    end = len(record.filing.text)
    bad = chunks[-1].model_copy(update={"char_start": end - 5, "char_end": end + 5})
    with pytest.raises(ValueError, match="past the end"):
        load_filing(db_conn, record, (*chunks[:-1], bad))


def test_chunk_whose_filing_fields_disagree_is_refused(db_conn, loaded):
    record, chunks = next(iter(loaded.values()))
    bad = chunks[0].model_copy(update={"fiscal_period": "FY1999"})
    with pytest.raises(ValueError, match="fiscal_period"):
        load_filing(db_conn, record, (bad, *chunks[1:]))


def test_chunk_id_that_disagrees_with_its_ordinal_is_refused(db_conn, loaded):
    record, chunks = next(iter(loaded.values()))
    bad = chunks[0].model_copy(update={"ordinal": 9999})
    with pytest.raises(ValueError, match="chunk_id"):
        load_filing(db_conn, record, (bad, *chunks[1:]))


def test_financial_statements_section_is_stored(db_conn, loaded):
    rows = dict(
        db_conn.execute("SELECT accession_no, financial_statements_section FROM filings")
    )
    for accession_no, (record, _) in loaded.items():
        assert rows[accession_no] == financial_statements_section(record.filing)


# ── financial_statements_section ──────────────────────────────────────────────

def test_nvidia_10k_statements_are_in_item_15(nvda_10k_filing):
    assert financial_statements_section(nvda_10k_filing) == "part_iv_item_15"


def test_amd_and_intel_10k_statements_are_in_item_8(amd_10k_filing, intc_10k_filing):
    assert financial_statements_section(amd_10k_filing) == "part_ii_item_8"
    assert financial_statements_section(intc_10k_filing) == "part_ii_item_8"


def test_10q_statements_are_in_part_i_item_1(nvda_10q_filing, intc_10q_filing):
    assert financial_statements_section(nvda_10q_filing) == "part_i_item_1"
    assert financial_statements_section(intc_10q_filing) == "part_i_item_1"


# ── resolve() ─────────────────────────────────────────────────────────────────

def test_resolve_returns_the_exact_text_of_every_chunk(db_conn, loaded):
    for record, chunks in loaded.values():
        text = record.filing.text
        for chunk in chunks:
            assert resolve(db_conn, chunk.chunk_id) == text[chunk.char_start : chunk.char_end]


def test_resolve_round_trips_on_a_seeded_random_sample_of_20(db_conn, loaded):
    """The RUNBOOK stop condition, on the fixtures."""
    population = [(r, c) for r, chunks in loaded.values() for c in chunks]
    sample = random.Random(SAMPLE_SEED).sample(population, SAMPLE_SIZE)
    for record, chunk in sample:
        expected = record.filing.text[chunk.char_start : chunk.char_end]
        assert resolve(db_conn, chunk.chunk_id) == expected


def test_resolve_handles_non_ascii_text(db_conn, loaded):
    hits = [
        (r, c)
        for r, chunks in loaded.values()
        for c in chunks
        if any(ord(ch) > 127 for ch in r.filing.text[c.char_start : c.char_end])
    ]
    assert hits, "fixtures contain non-ASCII text (curly quotes, dashes, ®)"
    record, chunk = hits[0]
    assert resolve(db_conn, chunk.chunk_id) == record.filing.text[chunk.char_start : chunk.char_end]


def test_stored_offsets_match_the_chunker_in_order(db_conn, loaded):
    record, chunks = next(iter(loaded.values()))
    expected = [(c.chunk_id, c.char_start, c.char_end) for c in chunks]
    assert stored_offsets(db_conn, record.meta.accession_no) == expected


def test_stored_offsets_for_an_unknown_filing_is_empty(db_conn, loaded):
    assert stored_offsets(db_conn, "0000000000-00-000000") == []


def test_resolve_unknown_chunk_raises(db_conn, loaded):
    with pytest.raises(ChunkNotFound):
        resolve(db_conn, "0000000000-00-000000:0000")
