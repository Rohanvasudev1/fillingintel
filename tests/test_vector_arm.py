"""The vector arm against the throwaway test schema (Step 5, tickets 03 and 04).

The corpus and fakes are in ``tests/vector_fakes.py``: the fake query vector
points along the first dimension, so the top 10 are the first 10 chunks by ID
among the chunks the question filter lets through.
"""
import math

import pytest

from ingest.chunker import CHUNKER_VERSION
from ingest.corpus import TICKER_BY_CIK
from ingest.store import get_chunk, resolve
from ingest.voyage import VoyageError
from retrieve.answer import DECLINE_TEXT, NOT_FOUND_TEXT
from retrieve.answer_model import AnswerModelError
from retrieve.answer_prompt import load_prompt
from retrieve.arm import ArmError
from retrieve.pricing import embedding_cost
from retrieve.query_cache import CacheError
from retrieve.vector import TOP_K, VectorArm
from tests.vector_fakes import (
    EXCERPT_ID,
    MODEL,
    STEP,
    FakeQueryEmbedder,
    ScriptedAnswerModel,
    embed_fixture_filings,
)
from tests.vector_fakes import unit as _unit

QUESTION = "What are the main supply chain risks?"  # names nothing, so no filter


def _arm(conn, embedder, answer_model=None, **kwargs) -> VectorArm:
    return VectorArm(conn, embedder, answer_model or ScriptedAnswerModel(), MODEL, **kwargs)


@pytest.fixture
def embedded(db_conn, fixture_records):
    return embed_fixture_filings(db_conn, fixture_records)


def test_top_ten_come_back_in_similarity_order_with_cosine_scores(db_conn, embedded):
    embedder = FakeQueryEmbedder()
    result = _arm(db_conn, embedder).run(QUESTION)
    assert [r.chunk_id for r in result.retrieved] == embedded[:TOP_K]
    assert [r.score for r in result.retrieved] == pytest.approx(
        [math.cos(i * STEP) for i in range(TOP_K)], abs=1e-6
    )
    assert embedder.calls == [(QUESTION, MODEL)]


def test_a_question_naming_nothing_is_unfiltered(db_conn, embedded):
    # A query vector at the last chunk's angle puts that chunk first; its neighbours
    # come from whichever filing they belong to.
    last = len(embedded) - 1
    result = _arm(db_conn, FakeQueryEmbedder(_unit(last * STEP))).run(QUESTION)
    expected = [embedded[last - i] for i in range(TOP_K)]
    assert [r.chunk_id for r in result.retrieved] == expected
    assert result.question_filter.accession_nos is None


def test_the_question_filter_narrows_retrieval(db_conn, embedded, nvda_10q_meta):
    question = "What does NVIDIA's 10-Q say about export controls?"
    result = _arm(db_conn, FakeQueryEmbedder()).run(question)
    in_10q = [c for c in embedded if c.startswith(nvda_10q_meta.accession_no + ":")]
    assert [r.chunk_id for r in result.retrieved] == in_10q[:TOP_K]
    assert result.question_filter.companies == ("NVDA",)
    assert result.question_filter.forms == ("10-Q",)
    assert result.question_filter.accession_nos == (nvda_10q_meta.accession_no,)


def test_named_companies_with_no_retrieved_chunk_are_reported(db_conn, embedded, intc_10k_meta):
    # Intel's accession number sorts before NVIDIA's, so at angle 0 the top 10 are all Intel's.
    question = "How do NVIDIA and Intel describe their reliance on TSMC?"
    result = _arm(db_conn, FakeQueryEmbedder()).run(question)
    assert all(r.chunk_id.startswith(intc_10k_meta.accession_no) for r in result.retrieved)
    assert result.companies_without_chunks == ("NVDA",)


def test_config_records_the_model_k_and_chunker_version(db_conn, embedded):
    arm = _arm(db_conn, FakeQueryEmbedder())
    assert arm.name == "vector"
    assert dict(arm.config.models) == {"embedding": MODEL, "answer": "claude-sonnet-5-5"}
    assert arm.config.k == TOP_K == 10
    assert arm.config.chunker_version == CHUNKER_VERSION


def test_latency_is_split_into_query_embedding_and_search(db_conn, embedded):
    ticks = iter([3.0, 3.1, 3.25])
    arm = _arm(db_conn, FakeQueryEmbedder(), clock=lambda: next(ticks))
    result = arm.run(QUESTION)
    assert result.embed_ms == pytest.approx(100.0)
    assert result.search_ms == pytest.approx(150.0)
    assert result.retrieval_ms == pytest.approx(250.0)
    assert result.query_cached is False


def test_a_cached_query_embedding_is_flagged(db_conn, embedded):
    embedder = FakeQueryEmbedder(cached=True)
    assert _arm(db_conn, embedder).run(QUESTION).query_cached is True


def test_a_chunk_without_a_vector_stops_the_arm_before_any_question(db_conn, embedded):
    db_conn.execute("DELETE FROM chunk_embeddings WHERE chunk_id = %s", (embedded[5],))
    db_conn.commit()
    with pytest.raises(ArmError, match="1 chunks have no voyage-4-large vector"):
        _arm(db_conn, FakeQueryEmbedder())


def test_a_stale_vector_stops_the_arm(db_conn, embedded):
    db_conn.execute(
        "UPDATE chunk_embeddings SET text_sha256 = %s WHERE chunk_id = %s", ("0" * 64, embedded[0])
    )
    db_conn.commit()
    with pytest.raises(ArmError, match="1 chunks have a stale"):
        _arm(db_conn, FakeQueryEmbedder())


def test_an_embedding_failure_is_an_arm_error(db_conn, embedded):
    arm = _arm(db_conn, FakeQueryEmbedder(error=VoyageError("HTTP 401")))
    with pytest.raises(ArmError, match="VoyageError"):
        arm.run(QUESTION)


def test_the_answer_model_sees_all_ten_chunks_with_their_text_and_source(db_conn, embedded):
    answer_model = ScriptedAnswerModel()
    _arm(db_conn, FakeQueryEmbedder(), answer_model).run(QUESTION)
    (request,) = answer_model.requests
    assert EXCERPT_ID.findall(request.user) == embedded[:TOP_K]
    first = get_chunk(db_conn, embedded[0])
    assert (
        f'<excerpt id="{first.chunk_id}" company="{TICKER_BY_CIK[first.cik]}" '
        f'form="{first.form_type}" '
        f'period="{first.fiscal_period}" section="{first.section}">\n'
        f"{resolve(db_conn, first.chunk_id)}\n</excerpt>"
    ) in request.user
    assert request.user.endswith(f"Question: {QUESTION}")


def test_the_result_carries_the_chunks_the_answer_model_saw_for_the_judges(db_conn, embedded):
    result = _arm(db_conn, FakeQueryEmbedder(), ScriptedAnswerModel()).run(QUESTION)
    assert [s.chunk_id for s in result.sources] == embedded[:TOP_K]
    assert result.sources[0].text == resolve(db_conn, embedded[0])


def test_citation_enforcement_drops_and_counts_the_right_sentences(db_conn, embedded):
    outside = embedded[-1]  # never in the top 10 at angle 0

    def script(ids: list[str]) -> str:
        return (f"STATUS: answered\nSupply is concentrated [{ids[0]}]. Lead times are long. "
                f"Costs rose [{ids[1]}][{outside}]. Demand is high [{ids[2]}].")

    result = _arm(db_conn, FakeQueryEmbedder(), ScriptedAnswerModel(script)).run(
        QUESTION
    )
    check = result.answer.citation_check
    assert [d.reason for d in check.dropped] == ["no_citation", "citation_not_retrieved"]
    assert result.answer.text == (f"Supply is concentrated [{embedded[0]}]. "
                                  f"Demand is high [{embedded[2]}].")
    assert (check.citations, check.citations_retrieved) == (4, 3)
    assert result.answer.generation_ms == 4200.0


def test_a_decline_passes_through_the_arm(db_conn, embedded):
    arm = _arm(db_conn, FakeQueryEmbedder(), ScriptedAnswerModel(lambda _: "STATUS: declined"))
    answer = arm.run("Should I buy NVIDIA stock?").answer
    assert (answer.status, answer.text) == ("declined", DECLINE_TEXT)


def test_config_records_the_answer_model_effort_and_prompt(db_conn, embedded):
    config = _arm(db_conn, FakeQueryEmbedder()).config
    assert config.models["answer"] == "claude-sonnet-5-5"
    assert dict(config.efforts) == {"answer": "high"}
    prompt = load_prompt("v1")
    assert config.answer_prompt == prompt
    assert config.answer_max_tokens == 16_000


def test_query_embedding_tokens_and_cost_are_recorded(db_conn, embedded):
    result = _arm(db_conn, FakeQueryEmbedder()).run(QUESTION)
    assert result.embed_tokens == 12
    assert result.embed_cost_usd == pytest.approx(embedding_cost(MODEL, 12))


def test_an_answer_model_failure_is_an_arm_error(db_conn, embedded):
    answer_model = ScriptedAnswerModel(error=AnswerModelError("HTTP 529"))
    arm = _arm(db_conn, FakeQueryEmbedder(), answer_model)
    with pytest.raises(ArmError, match="AnswerModelError"):
        arm.run(QUESTION)


def test_a_not_found_answer_passes_through_the_arm(db_conn, embedded):
    script = ScriptedAnswerModel(lambda ids: f"STATUS: not_found\nSupply is tight [{ids[0]}].")
    answer = _arm(db_conn, FakeQueryEmbedder(), script).run(QUESTION).answer
    assert answer.status == "not_found"
    assert answer.text == f"{NOT_FOUND_TEXT} Supply is tight [{embedded[0]}]."


def test_an_unpriced_embedding_model_stops_the_arm_before_any_question(db_conn, embedded):
    with pytest.raises(ArmError, match="no price"):
        VectorArm(db_conn, FakeQueryEmbedder(), ScriptedAnswerModel(), "voyage-unpriced")


def test_retrieve_returns_the_same_retrieval_as_run(db_conn, embedded):
    question = "How do NVIDIA and Intel describe their reliance on TSMC?"
    ticks = iter([3.0, 3.1, 3.25, 5.0, 5.1, 5.25])
    arm = _arm(db_conn, FakeQueryEmbedder(), clock=lambda: next(ticks))
    retrieval = arm.retrieve(question)
    result = arm.run(question)
    assert retrieval.retrieved == result.retrieved
    assert retrieval.question_filter == result.question_filter
    assert retrieval.companies_without_chunks == result.companies_without_chunks == ("NVDA",)
    assert (retrieval.embed_ms, retrieval.search_ms) == pytest.approx(
        (result.embed_ms, result.search_ms)
    )
    assert (retrieval.query_cached, retrieval.embed_tokens, retrieval.embed_cost_usd) == (
        result.query_cached, result.embed_tokens, result.embed_cost_usd
    )
    assert retrieval.retrieval_ms == pytest.approx(250.0)


def test_retrieve_makes_no_answer_model_call(db_conn, embedded):
    answer_model = ScriptedAnswerModel()
    _arm(db_conn, FakeQueryEmbedder(), answer_model).retrieve(QUESTION)
    assert answer_model.requests == []


def test_retrieve_works_with_no_answer_model_and_run_refuses(db_conn, embedded):
    arm = VectorArm(db_conn, FakeQueryEmbedder(), None, MODEL)
    retrieval = arm.retrieve(QUESTION)
    assert [r.chunk_id for r in retrieval.retrieved] == embedded[:TOP_K]
    with pytest.raises(ArmError, match="no answer model"):
        arm.run(QUESTION)


def test_retrieve_respects_the_arms_k(db_conn, embedded):
    retrieval = _arm(db_conn, FakeQueryEmbedder(), k=1).retrieve(QUESTION)
    assert [r.chunk_id for r in retrieval.retrieved] == embedded[:1]


@pytest.mark.parametrize(
    ("error", "name"),
    [(VoyageError("HTTP 401"), "VoyageError"), (CacheError("bad cache file"), "CacheError"),
     (OSError("disk full"), "OSError")],
)
def test_retrieve_raises_embedding_and_cache_failures_as_arm_errors(db_conn, embedded, error,
                                                                    name):
    arm = _arm(db_conn, FakeQueryEmbedder(error=error))
    with pytest.raises(ArmError, match=name):
        arm.retrieve(QUESTION)


def test_retrieve_raises_a_database_failure_as_an_arm_error(db_conn, embedded):
    arm = _arm(db_conn, FakeQueryEmbedder(vector=(1.0, 0.0, 0.0)))  # wrong dimension
    try:
        with pytest.raises(ArmError, match=r"vector arm failed \(\w+\): "):
            arm.retrieve(QUESTION)
    finally:
        db_conn.rollback()
