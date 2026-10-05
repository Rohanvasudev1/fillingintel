"""The vector arm against the throwaway test schema (Step 5, tickets 03 and 04).

Every chunk in the schema gets a unit vector at angle ``i * STEP`` in the plane
of the first two dimensions, ``i`` being its position in chunk-ID order.  The
fake query vector points along the first dimension, so cosine similarity is
``cos(i * STEP)``: the top 10 must be the first 10 chunks by ID, in that order,
among the chunks the question filter lets through.
"""
import math

import pytest

from ingest.chunker import CHUNKER_VERSION, chunk_filing
from ingest.store import chunks_for_embedding, load_filing, save_embedding
from ingest.voyage import Embedding, VoyageError
from retrieve.arm import ArmError
from retrieve.vector import TOP_K, VectorArm
from tests.conftest import FIXTURE_NAMES

MODEL = "voyage-4-large"
DIMS = 1024
STEP = 0.001  # radians; stays below pi/2 for fewer than 1,500 chunks, so the order is strict
QUESTION = "What are the main supply chain risks?"  # names nothing, so no filter
FILTER_FIXTURES = ("nvda_10k", "nvda_10q", "intc_10k")


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
    """NVIDIA's two fixture filings and Intel's 10-K loaded, and every chunk in the
    schema embedded; returns chunk IDs in order."""
    db_conn.execute("DELETE FROM chunk_embeddings")
    db_conn.commit()
    for name in FILTER_FIXTURES:
        record = fixture_records[FIXTURE_NAMES.index(name)]
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


def test_a_question_naming_nothing_is_unfiltered(db_conn, embedded):
    # A query vector at the last chunk's angle puts that chunk first; its neighbours
    # come from whichever filing they belong to.
    last = len(embedded) - 1
    result = VectorArm(db_conn, FakeQueryEmbedder(_unit(last * STEP)), MODEL).run(QUESTION)
    expected = [embedded[last - i] for i in range(TOP_K)]
    assert [r.chunk_id for r in result.retrieved] == expected
    assert result.question_filter.accession_nos is None


def test_the_question_filter_narrows_retrieval(db_conn, embedded, nvda_10q_meta):
    question = "What does NVIDIA's 10-Q say about export controls?"
    result = VectorArm(db_conn, FakeQueryEmbedder(), MODEL).run(question)
    in_10q = [c for c in embedded if c.startswith(nvda_10q_meta.accession_no + ":")]
    assert [r.chunk_id for r in result.retrieved] == in_10q[:TOP_K]
    assert result.question_filter.companies == ("NVDA",)
    assert result.question_filter.forms == ("10-Q",)
    assert result.question_filter.accession_nos == (nvda_10q_meta.accession_no,)


def test_named_companies_with_no_retrieved_chunk_are_reported(db_conn, embedded, intc_10k_meta):
    # Intel's accession number sorts before NVIDIA's, so at angle 0 the top 10 are all Intel's.
    question = "How do NVIDIA and Intel describe their reliance on TSMC?"
    result = VectorArm(db_conn, FakeQueryEmbedder(), MODEL).run(question)
    assert all(r.chunk_id.startswith(intc_10k_meta.accession_no) for r in result.retrieved)
    assert result.companies_without_chunks == ("NVDA",)


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
