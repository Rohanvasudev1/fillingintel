"""``python -m eval.validate --db``: gold and hard negatives checked through resolve() (Step 4)."""
import pytest

from eval.corpus_index import ChunkIndex
from eval.validate import check_against_db
from ingest.chunker import chunk_filing
from ingest.store import load_filing
from tests.eval_helpers import make_record


@pytest.fixture(scope="module")
def index(fixture_records):
    return ChunkIndex.from_records(fixture_records)


@pytest.fixture(scope="module")
def loaded(db_conn, fixture_records):
    for record in fixture_records:
        load_filing(db_conn, record, chunk_filing(record.filing))
    return db_conn


def test_records_whose_chunks_resolve_pass(loaded, index):
    assert check_against_db([make_record(index)], loaded) == []


def test_gold_chunk_missing_from_the_database_is_a_problem(loaded, index):
    record = make_record(index)
    gold = "0000000000-00-000000:0000"
    bad = record.model_copy(
        update={"gold_chunk_ids": [gold], "gold_text_sha256": {gold: "0" * 64}}
    )
    assert any("not in the database" in p for p in check_against_db([bad], loaded))


def test_gold_text_that_differs_in_the_database_is_a_problem(loaded, index):
    record = make_record(index)
    gold = record.gold_chunk_ids[0]
    bad = record.model_copy(update={"gold_text_sha256": {gold: "0" * 64}})
    assert any("differs in the database" in p for p in check_against_db([bad], loaded))
