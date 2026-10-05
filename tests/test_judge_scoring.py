"""Judged scores for one answer and one run, with real Ragas metrics (Step 5, ticket 07).

The judge backend is scripted by output schema and the embedder is a lookup
table, so every score below is computed by hand.  Ragas's own faithfulness and
answer relevancy code runs unchanged.
"""
import math

import pytest

from eval.judging.openai_backend import parse_openai_reply, total_usage
from eval.judging.openai_judge import JUDGE_MODEL
from eval.judging.scoring import JudgeInput, RagasJudges, metrics_for
from retrieve.answer import write_answer
from retrieve.answer_model import ApiResponse
from retrieve.answer_prompt import SourceChunk, load_prompt
from retrieve.pricing import embedding_cost, openai_cost
from tests.anthropic_fixtures import load, with_text
from tests.judge_fakes import FixedEmbedder, ScriptedBackend, prompt_of, schema_title
from tests.openai_fixtures import RECORDED, with_refusal

FIXTURE = load("answered_q0072")
QUESTION = FIXTURE["question"]
A, B = FIXTURE["retrieved_chunk_ids"][3], FIXTURE["retrieved_chunk_ids"][4]
SOURCES = (
    SourceChunk(A, "NVDA", "10-K", "FY2026", "part_ii_item_8", "Customer A was 22% of revenue."),
    SourceChunk(B, "NVDA", "10-K", "FY2026", "part_ii_item_8", "Customer B was 14% of revenue."),
)
GENERATED = "What share of NVIDIA's revenue came from its two largest customers?"
EMBED_TOKENS = 5


class _Replay:
    def __init__(self, text: str):
        self._response = ApiResponse(with_text(FIXTURE["response"], text), 1.0)

    def complete(self, request) -> ApiResponse:
        return self._response


def _answer(text: str):
    return write_answer(QUESTION, SOURCES, _Replay(text), load_prompt("v1"))


ANSWERED = _answer(f"STATUS: answered\nCustomer A was 22% [{A}]. Customer B was 14% [{B}].")


def _statements(prompt: str) -> dict:
    return {"statements": ["Customer A was 22%.", "Customer B was 14%.", "Together 36%."]}


def _replies(**overrides) -> dict:
    replies = {
        "StatementGeneratorOutput": _statements,
        "NLIStatementOutput": {"statements": [
            {"statement": "Customer A was 22%.", "reason": "stated", "verdict": 1},
            {"statement": "Customer B was 14%.", "reason": "stated", "verdict": 1},
            {"statement": "Together 36%.", "reason": "not stated", "verdict": 0},
        ]},
        "AnswerRelevanceOutput": {"question": GENERATED, "noncommittal": 0},
        "CitationSupportOutput": {"verdicts": [
            {"sentence": 1, "reason": "stated", "supported": True},
            {"sentence": 2, "reason": "wrong figure", "supported": False},
        ]},
        "BehaviourVerdict": {"reason": "it declined", "correct": True},
    }
    return {**replies, **overrides}


def _judges(backend, vectors=None):
    vectors = vectors or {QUESTION: (1.0, 0.0), GENERATED: (0.6, 0.8)}
    return RagasJudges(backend, FixedEmbedder(vectors, tokens=EMBED_TOKENS))


def _item(answer=ANSWERED, class_="lookup") -> JudgeInput:
    return JudgeInput(QUESTION, class_, answer, SOURCES)


def test_answerable_classes_get_faithfulness_relevancy_and_citation_support():
    for cls in ("lookup", "local", "multi_hop", "global"):
        assert metrics_for(cls) == ("faithfulness", "answer_relevancy", "citation_support")
    assert metrics_for("decline") == ("decline_correct",)
    assert metrics_for("unanswerable") == ("not_found_correct",)


def test_hand_computed_scores_for_an_answered_question():
    scores = _judges(ScriptedBackend(_replies())).judge(_item(), run=1)
    assert scores.run == 1
    assert scores.scores == pytest.approx({
        "faithfulness": 2 / 3,  # 2 of 3 statements inferred from the context
        "answer_relevancy": 0.6,  # cosine of (1, 0) and (0.6, 0.8), for each of 3 questions
        "citation_support": 0.5,  # 1 of 2 cited sentences supported
    })
    assert scores.errors == {}


def test_faithfulness_judges_the_kept_sentences_without_citation_markers_against_every_chunk():
    backend = ScriptedBackend(_replies())
    _judges(backend).judge(_item(), run=1)
    prompts = {schema_title(r): prompt_of(r) for r in backend.requests}
    statement_prompt = prompts["StatementGeneratorOutput"]
    assert "Customer A was 22%. Customer B was 14%." in statement_prompt
    assert A not in statement_prompt
    assert all(s.text in prompts["NLIStatementOutput"] for s in SOURCES)


def test_answer_relevancy_asks_three_times_and_each_ask_is_a_separate_call():
    backend = ScriptedBackend(_replies())
    _judges(backend).judge(_item(), run=2)
    asks = [r for r in backend.requests if schema_title(r) == "AnswerRelevanceOutput"]
    assert [(r.run, r.repeat) for r in asks] == [(2, 0), (2, 1), (2, 2)]


def test_a_noncommittal_answer_scores_zero_relevancy():
    replies = _replies(AnswerRelevanceOutput={"question": GENERATED, "noncommittal": 1})
    scores = _judges(ScriptedBackend(replies)).judge(_item(), run=1)
    assert scores.scores["answer_relevancy"] == 0.0


def test_citation_support_shows_each_sentence_with_the_text_of_its_cited_chunks():
    backend = ScriptedBackend(_replies())
    _judges(backend).judge(_item(), run=1)
    (request,) = [r for r in backend.requests if schema_title(r) == "CitationSupportOutput"]
    prompt = prompt_of(request)
    assert f"1. Customer A was 22% [{A}]." in prompt
    assert f'<excerpt id="{B}">\nCustomer B was 14% of revenue.\n</excerpt>' in prompt


def test_verdicts_that_do_not_number_every_sentence_are_a_recorded_error():
    replies = _replies(CitationSupportOutput={"verdicts": [
        {"sentence": 1, "reason": "stated", "supported": True}]})
    scores = _judges(ScriptedBackend(replies)).judge(_item(), run=1)
    assert scores.scores["citation_support"] is None
    assert "sentence" in scores.errors["citation_support"]
    assert scores.scores["faithfulness"] == pytest.approx(2 / 3)  # the others still score


def test_a_judge_refusal_is_recorded_against_each_metric_not_raised():
    backend = ScriptedBackend(_replies(), body=with_refusal(RECORDED))
    scores = _judges(backend).judge(_item(), run=1)
    assert set(scores.scores.values()) == {None}
    assert set(scores.errors) == {"faithfulness", "answer_relevancy", "citation_support"}
    assert all("refused" in e for e in scores.errors.values())


def test_an_answer_with_no_kept_sentences_is_not_scored_for_faithfulness_or_support():
    not_found = _answer("STATUS: not_found\nNothing here.")  # the uncited sentence is dropped
    backend = ScriptedBackend(_replies())
    scores = _judges(backend).judge(_item(not_found), run=1)
    assert scores.scores["faithfulness"] is None and scores.scores["citation_support"] is None
    assert scores.errors == {}
    # relevancy still judges what a reader sees: the fixed not-found sentence
    assert scores.scores["answer_relevancy"] == pytest.approx(0.6)
    assert {schema_title(r) for r in backend.requests} == {"AnswerRelevanceOutput"}


def test_no_statements_gives_no_faithfulness_score():
    replies = _replies(StatementGeneratorOutput={"statements": []})
    scores = _judges(ScriptedBackend(replies)).judge(_item(), run=1)
    assert scores.scores["faithfulness"] is None
    assert not math.isnan(scores.scores["citation_support"])


@pytest.mark.parametrize(("class_", "metric", "expected"), [
    ("decline", "decline_correct", "decline"),
    ("unanswerable", "not_found_correct", "do not contain"),
])
def test_decline_and_unanswerable_records_get_a_behaviour_verdict(class_, metric, expected):
    backend = ScriptedBackend(_replies())
    declined = _answer("STATUS: declined")
    scores = _judges(backend).judge(_item(declined, class_), run=1)
    assert scores.scores == {metric: 1.0}
    (request,) = backend.requests
    prompt = prompt_of(request)
    assert expected in prompt
    assert declined.text in prompt and QUESTION in prompt


def test_an_incorrect_behaviour_scores_zero():
    replies = _replies(BehaviourVerdict={"reason": "it answered", "correct": False})
    scores = _judges(ScriptedBackend(replies)).judge(_item(ANSWERED, "decline"), run=1)
    assert scores.scores == {"decline_correct": 0.0}


def test_cost_counts_every_judge_call_and_the_relevancy_embeddings():
    scores = _judges(ScriptedBackend(_replies())).judge(_item(), run=1)
    # 2 faithfulness + 3 relevancy + 1 citation support calls; 4 embeddings
    usage = parse_openai_reply(RECORDED).usage
    assert scores.calls == 6
    assert scores.cost_usd == pytest.approx(
        6 * openai_cost(JUDGE_MODEL, usage) + embedding_cost("voyage-4-large", 4 * EMBED_TOKENS))
    assert scores.usage == total_usage([usage] * 6)
    assert scores.usage.reasoning_tokens == 6 * usage.reasoning_tokens
