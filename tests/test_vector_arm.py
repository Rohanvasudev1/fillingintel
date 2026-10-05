"""The vector arm against the throwaway test schema (Step 5, ticket 03).

Every chunk in the schema gets a unit vector at angle ``i * STEP`` in the plane
of the first two dimensions, ``i`` being its position in chunk-ID order.  The
fake query vector points along the first dimension, so cosine similarity is
``cos(i * STEP)``: the top 10 must be the first 10 chunks by ID, in that order.
"""
import math

import pytest

from ingest.chunker import CHUNKER_VERSION, chunk_filing
from ingest.store import chunks_for_embedding, load_filing, save_embedding
from ingest.voyage import Embedding, VoyageError
from retrieve.arm import ArmError
from retrieve.vector import TOP_K, VectorArm

MODEL = "voyage-4-large"
DIMS = 1024
STEP = 0.001  # radians; stays below pi/2 for fewer than 1,500 chunks, so the order is strict
QUESTION = "What was AMD's revenue in fiscal 2025?"


def _unit(angle: float) -> tuple[float, ...]:
    return (math.cos(angle), math.sin(angle)) + (0.0,) * (DIMS - 2)


class FakeQueryEmbedder:
    def __init__(self, vector=_unit(0.0), error: Exception | None = None, cached=False):
        self.vector = vector
        self.cached = cached
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def embed_query(self, text: str, model: str) -> Embedding:
        self.calls.append((text, model))
        if self.error is not None:
            raise self.error
        return Embedding(vector=self.vector, api_token_count=12, from_cache=self.cached)


@pytest.fixture
def embedded(db_conn, fixture_records):
    """Two fixture filings loaded and every chunk in the schema embedded; returns IDs in order."""
    db_conn.execute("DELETE FROM chunk_embeddings")
    db_conn.commit()
    for record in fixture_records[:2]:
        load_filing(db_conn, record, chunk_filing(record.filing))
    chunks = chunks_for_embedding(db_conn, MODEL)
    assert len(chunks) < math.pi / 2 / STEP
    for i, chunk in enumerate(chunks):
        save_embedding(db_conn, chunk.chunk_id, MODEL, chunk.text, _unit(i * STEP), 1)
    return [c.chunk_id for c in chunks]


def test_top_ten_come_back_in_similarity_order_with_cosine_scores(db_conn, embedded):
    embedder = FakeQueryEmbedder()
    result = VectorArm(db_conn, embedder, MODEL).run(QUESTION)
    assert [r.chunk_id for r in result.retrieved] == embedded[:TOP_K]
    assert [r.score for r in result.retrieved] == pytest.approx(
        [math.cos(i * STEP) for i in range(TOP_K)], abs=1e-6
    )
    assert embedder.calls == [(QUESTION, MODEL)]


def test_retrieval_is_unfiltered(db_conn, embedded):
    # A query vector at the last chunk's angle puts that chunk first; its neighbours
    # come from whichever filing they belong to.
    last = len(embedded) - 1
    result = VectorArm(db_conn, FakeQueryEmbedder(_unit(last * STEP)), MODEL).run(QUESTION)
    expected = [embedded[last - i] for i in range(TOP_K)]
    assert [r.chunk_id for r in result.retrieved] == expected


def test_config_records_the_model_k_and_chunker_version(db_conn, embedded):
    arm = VectorArm(db_conn, FakeQueryEmbedder(), MODEL)
    assert arm.name == "vector"
    assert dict(arm.config.models) == {"embedding": MODEL}
    assert arm.config.k == TOP_K == 10
    assert arm.config.chunker_version == CHUNKER_VERSION


def test_latency_is_split_into_query_embedding_and_search(db_conn, embedded):
    ticks = iter([3.0, 3.1, 3.25])
    arm = VectorArm(db_conn, FakeQueryEmbedder(), MODEL, clock=lambda: next(ticks))
    result = arm.run(QUESTION)
    assert result.embed_ms == pytest.approx(100.0)
    assert result.search_ms == pytest.approx(150.0)
    assert result.retrieval_ms == pytest.approx(250.0)
    assert result.query_cached is False


def test_a_cached_query_embedding_is_flagged(db_conn, embedded):
    embedder = FakeQueryEmbedder(cached=True)
    assert VectorArm(db_conn, embedder, MODEL).run(QUESTION).query_cached is True


def test_a_chunk_without_a_vector_stops_the_arm_before_any_question(db_conn, embedded):
    db_conn.execute("DELETE FROM chunk_embeddings WHERE chunk_id = %s", (embedded[5],))
    db_conn.commit()
    with pytest.raises(ArmError, match="1 chunks have no voyage-4-large vector"):
        VectorArm(db_conn, FakeQueryEmbedder(), MODEL)


def test_a_stale_vector_stops_the_arm(db_conn, embedded):
    db_conn.execute(
        "UPDATE chunk_embeddings SET text_sha256 = %s WHERE chunk_id = %s", ("0" * 64, embedded[0])
    )
    db_conn.commit()
    with pytest.raises(ArmError, match="1 chunks have a stale"):
        VectorArm(db_conn, FakeQueryEmbedder(), MODEL)


def test_an_embedding_failure_is_an_arm_error(db_conn, embedded):
    arm = VectorArm(db_conn, FakeQueryEmbedder(error=VoyageError("HTTP 401")), MODEL)
    with pytest.raises(ArmError, match="VoyageError"):
        arm.run(QUESTION)
