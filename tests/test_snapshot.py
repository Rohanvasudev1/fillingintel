"""The corpus snapshot and the snapshot query embedder (Step 6, ticket 02, ADR-0003).

Round trips run between two throwaway schemas of their own, so rows other tests
leave in the shared test schema do not matter.  The committed snapshot is checked
against the local database and query cache only when both are present.
"""
import gzip
import math
import os
import re
from pathlib import Path

import pytest

from eval.question_sets import agent_drafted_set, load_question_sets
from eval.schema import NO_GOLD_CLASSES
from eval.snapshot import (
    SNAPSHOT_PATH,
    QueryVector,
    SnapshotError,
    SnapshotQueryEmbedder,
    build_snapshot,
    encode_snapshot,
    gated_questions,
    load_snapshot,
    query_vectors_from_cache,
    read_snapshot,
)
from ingest.chunker import chunk_filing
from ingest.parsed_files import text_sha256
from ingest.store import (
    chunks_for_embedding,
    export_rows,
    load_filing,
    save_embedding,
)
from ingest.voyage import Embedding
from retrieve.query_cache import DEFAULT_CACHE_DIR, CachedQueryEmbedder, CacheError
from tests.conftest import FIXTURE_NAMES, throwaway_schema

MODEL = "voyage-4-large"
DIMS = 1024
SOURCE_FIXTURES = ("nvda_10q", "amd_10q")
GATED_DEV_QUESTIONS = 82
QUESTIONS = ("What was NVIDIA's revenue?", "What are AMD's main risks?")


def _vector(seed: int) -> tuple[float, ...]:
    """A unit vector with irregular components, so float round trips are exercised."""
    raw = [math.sin(seed * 7.3 + j * 0.37) for j in range(DIMS)]
    norm = math.sqrt(sum(x * x for x in raw))
    return tuple(x / norm for x in raw)


def _query_vectors(questions=QUESTIONS) -> tuple[QueryVector, ...]:
    return tuple(
        QueryVector(MODEL, text_sha256(q), _vector(100 + i), 9) for i, q in enumerate(questions)
    )


@pytest.fixture(scope="module")
def source(fixture_records):
    """Two fixture filings loaded and every chunk embedded, in a schema of their own."""
    with throwaway_schema() as conn:
        for name in SOURCE_FIXTURES:
            record = fixture_records[FIXTURE_NAMES.index(name)]
            load_filing(conn, record, chunk_filing(record.filing))
        for i, chunk in enumerate(chunks_for_embedding(conn, MODEL)):
            save_embedding(conn, chunk.chunk_id, MODEL, chunk.text, _vector(i), 1 + i % 50)
        yield conn


@pytest.fixture
def target():
    with throwaway_schema() as conn:
        yield conn


@pytest.fixture(scope="module")
def snapshot_bytes(source):
    return build_snapshot(source, MODEL, _query_vectors())


def _write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "snapshot.jsonl.gz"
    path.write_bytes(data)
    return path


def _row_counts(conn) -> tuple[int, int, int]:
    return tuple(
        conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("filings", "chunks", "chunk_embeddings")
    )


# ── building ──────────────────────────────────────────────────────────────────

def test_rebuilding_from_unchanged_data_gives_identical_bytes(source, snapshot_bytes):
    assert build_snapshot(source, MODEL, _query_vectors()) == snapshot_bytes


def test_query_vectors_are_stored_in_hash_order_whatever_order_they_come_in(source, snapshot_bytes):
    assert build_snapshot(source, MODEL, _query_vectors()[::-1]) == snapshot_bytes


def test_the_snapshot_holds_no_question_text(snapshot_bytes):
    body = gzip.decompress(snapshot_bytes).decode("utf-8")
    assert not any(q in body for q in QUESTIONS)


def test_building_refuses_a_chunk_without_a_vector(target, fixture_records):
    record = fixture_records[FIXTURE_NAMES.index("nvda_10q")]
    load_filing(target, record, chunk_filing(record.filing))
    with pytest.raises(SnapshotError, match="vector"):
        build_snapshot(target, MODEL, _query_vectors())


# ── loading ───────────────────────────────────────────────────────────────────

def test_loading_into_an_empty_schema_gives_identical_rows(
    source, target, snapshot_bytes, tmp_path
):
    loaded = load_snapshot(target, _write(tmp_path, snapshot_bytes))
    assert export_rows(target, MODEL) == export_rows(source, MODEL)
    assert build_snapshot(target, MODEL, loaded.query_vectors) == snapshot_bytes


def test_loading_returns_the_file_hash_model_and_query_vectors(
    target, snapshot_bytes, tmp_path
):
    import hashlib

    loaded = load_snapshot(target, _write(tmp_path, snapshot_bytes))
    assert loaded.sha256 == hashlib.sha256(snapshot_bytes).hexdigest()
    assert loaded.model == MODEL
    assert loaded.query_vectors == tuple(sorted(_query_vectors(), key=lambda v: v.text_sha256))


def test_a_changed_byte_fails_loudly_and_loads_nothing(target, snapshot_bytes, tmp_path):
    damaged = bytearray(snapshot_bytes)
    damaged[len(damaged) // 2] ^= 0x01
    with pytest.raises(SnapshotError):
        load_snapshot(target, _write(tmp_path, bytes(damaged)))
    assert _row_counts(target) == (0, 0, 0)


def test_a_hand_edited_body_fails_its_hash_check(snapshot_bytes, tmp_path):
    lines = gzip.decompress(snapshot_bytes).decode("utf-8").split("\n")
    lines[-2] = lines[-2].replace('"api_token_count":9', '"api_token_count":8')
    edited = gzip.compress("\n".join(lines).encode("utf-8"), mtime=0)
    with pytest.raises(SnapshotError, match="hash"):
        read_snapshot(_write(tmp_path, edited))


def test_rows_that_fail_a_schema_check_fail_loudly_and_load_nothing(
    source, target, tmp_path
):
    rows = export_rows(source, MODEL)
    bad_filing = {**rows.filings[0], "text_sha256": "0" * 64}
    bad = rows._replace(filings=(bad_filing, *rows.filings[1:]))
    data = encode_snapshot(bad, MODEL, _query_vectors())
    with pytest.raises(SnapshotError, match="check"):
        load_snapshot(target, _write(tmp_path, data))
    assert _row_counts(target) == (0, 0, 0)


def test_a_chunk_id_that_disagrees_with_its_ordinal_fails_loudly(source, target, tmp_path):
    rows = export_rows(source, MODEL)
    bad_chunk = {**rows.chunks[0], "ordinal": rows.chunks[0]["ordinal"] + 100_000}
    data = encode_snapshot(rows._replace(chunks=(bad_chunk, *rows.chunks[1:])), MODEL, ())
    with pytest.raises(SnapshotError, match="check"):
        load_snapshot(target, _write(tmp_path, data))
    assert _row_counts(target) == (0, 0, 0)


def test_two_different_vectors_for_one_question_are_refused(source):
    first = _query_vectors()[0]
    clash = QueryVector(first.model, first.text_sha256, _vector(999), first.api_token_count)
    with pytest.raises(SnapshotError, match="share"):
        encode_snapshot(export_rows(source, MODEL), MODEL, (first, clash))


def test_a_vector_made_from_other_text_fails_loudly(source, target, tmp_path):
    rows = export_rows(source, MODEL)
    stale = {**rows.embeddings[0], "text_sha256": "f" * 64}
    data = encode_snapshot(rows._replace(embeddings=(stale, *rows.embeddings[1:])), MODEL, ())
    with pytest.raises(SnapshotError, match="stale"):
        load_snapshot(target, _write(tmp_path, data))
    assert _row_counts(target) == (0, 0, 0)


def test_loading_refuses_a_schema_that_already_holds_filings(
    source, snapshot_bytes, tmp_path
):
    with pytest.raises(SnapshotError, match="empty"):
        load_snapshot(source, _write(tmp_path, snapshot_bytes))


# ── query vectors ─────────────────────────────────────────────────────────────

def test_the_gated_questions_are_the_answerable_dev_questions():
    records = agent_drafted_set(load_question_sets()).records
    gated = gated_questions(records)
    expected = [r.question for r in records if r.split == "dev" and r.class_ not in NO_GOLD_CLASSES]
    assert list(gated) == expected
    assert len(gated) == GATED_DEV_QUESTIONS


class _FakeVoyage:
    def __init__(self):
        self.calls = 0

    def embed_query(self, text: str, model: str) -> Embedding:
        self.calls += 1
        return Embedding(vector=_vector(len(text)), api_token_count=7)


def test_query_vectors_come_from_the_cache_without_calling_voyage(tmp_path):
    voyage = _FakeVoyage()
    CachedQueryEmbedder(voyage, tmp_path).embed_query(QUESTIONS[0], MODEL)
    (vector,) = query_vectors_from_cache(QUESTIONS[:1], MODEL, tmp_path)
    assert voyage.calls == 1  # only the call that filled the cache
    assert vector == QueryVector(MODEL, text_sha256(QUESTIONS[0]), _vector(len(QUESTIONS[0])), 7)


def test_a_question_missing_from_the_cache_is_an_error(tmp_path):
    with pytest.raises(SnapshotError, match="not in the query cache"):
        query_vectors_from_cache(QUESTIONS[:1], MODEL, tmp_path)


def test_the_embedder_returns_the_stored_vector_for_a_gated_question():
    embedding = SnapshotQueryEmbedder(_query_vectors()).embed_query(QUESTIONS[1], MODEL)
    assert embedding == Embedding(vector=_vector(101), api_token_count=9, from_cache=True)


@pytest.mark.parametrize(("question", "model"), [
    ("A question the gate does not ask?", MODEL),
    (QUESTIONS[0], "voyage-3"),
])
def test_the_embedder_raises_for_any_other_question_or_model(question, model):
    with pytest.raises(SnapshotError, match="no snapshot vector"):
        SnapshotQueryEmbedder(_query_vectors()).embed_query(question, model)


def test_a_missing_vector_is_a_query_cache_error_so_the_arm_reports_it():
    # VectorArm.retrieve() turns CacheError into ArmError (ticket 01).
    with pytest.raises(CacheError):
        SnapshotQueryEmbedder(_query_vectors()).embed_query("Not gated?", MODEL)


# ── the committed snapshot ────────────────────────────────────────────────────

_SECRET = re.compile(r"sk-ant-|sk-proj-|\bpa-[A-Za-z0-9_-]{20,}|Bearer\s|postgres(ql)?://")


@pytest.fixture(scope="module")
def committed():
    return read_snapshot(SNAPSHOT_PATH)


def test_the_committed_snapshot_holds_only_the_gated_query_vectors(committed):
    records = agent_drafted_set(load_question_sets()).records
    expected = sorted(text_sha256(q) for q in gated_questions(records))
    assert [v.text_sha256 for v in committed.query_vectors] == expected
    assert {v.model for v in committed.query_vectors} == {MODEL}


def test_the_committed_snapshot_holds_no_secrets():
    body = gzip.decompress(SNAPSHOT_PATH.read_bytes()).decode("utf-8")
    assert _SECRET.search(body) is None
    for name in ("VOYAGE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "POSTGRES_PASSWORD"):
        value = os.environ.get(name)
        if value:
            assert value not in body, name


def test_the_committed_snapshot_matches_the_local_database_and_query_cache():
    import psycopg

    url = os.environ.get("DATABASE_URL")
    if not url or not (DEFAULT_CACHE_DIR / MODEL).is_dir():
        pytest.skip("no local database and query cache (CI)")
    with psycopg.connect(url) as conn:
        if conn.execute("SELECT to_regclass('public.filings')").fetchone()[0] is None:
            pytest.skip("the local database has no filings table")
        conn.execute("SET search_path TO public")
        records = agent_drafted_set(load_question_sets()).records
        vectors = query_vectors_from_cache(gated_questions(records), MODEL, DEFAULT_CACHE_DIR)
        assert build_snapshot(conn, MODEL, vectors) == SNAPSHOT_PATH.read_bytes()


def test_the_committed_snapshot_loads_into_an_empty_schema(target, committed):
    loaded = load_snapshot(target, SNAPSHOT_PATH)
    assert loaded.sha256 == committed.sha256
    assert _row_counts(target) == tuple(
        len(rows) for rows in (committed.rows.filings, committed.rows.chunks,
                               committed.rows.embeddings)
    )
