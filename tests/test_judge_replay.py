"""The real judges replay recorded gpt-6-luna and Voyage responses (Step 5, ticket 09).

tests/fixtures/judge/ holds what scripts/capture_judge_responses.py recorded on
2026-10-05: judge run 1 on three recorded answers, through the same response
and query caches the harness uses.  Here the caches point at those files and
the backends refuse every call, so the scores below come from the real API
replies, parsed and scored offline by Ragas and the project's judges.
"""
import json
from pathlib import Path

import pytest

from eval.judging.scoring import JudgeInput, RagasJudges
from retrieve.answer import write_answer
from retrieve.answer_model import ApiResponse
from retrieve.answer_prompt import SourceChunk, load_prompt
from retrieve.query_cache import CachedQueryEmbedder
from retrieve.response_cache import CachedAnswerModel
from scripts.capture_judge_responses import SECRET_PATTERN
from tests.anthropic_fixtures import load

FIXTURES = Path(__file__).parent / "fixtures" / "judge"
SOURCES = json.loads((FIXTURES / "inputs.json").read_text(encoding="utf-8"))["sources"]


class _Offline:
    def complete(self, request):
        raise AssertionError("a judge call missed the recorded responses")

    def embed_query(self, text, model):
        raise AssertionError(f"an embedding missed the recorded vectors: {text[:60]}")


class _Replay:
    def __init__(self, body: dict):
        self._response = ApiResponse(body, 0.0)

    def complete(self, request) -> ApiResponse:
        return self._response


def _judge(name: str, class_: str):
    fixture = load(name)
    chunks = tuple(SourceChunk(**s) for s in SOURCES[name])
    answer = write_answer(fixture["question"], chunks, _Replay(fixture["response"]),
                          load_prompt("v1"))
    judges = RagasJudges(CachedAnswerModel(_Offline(), FIXTURES / "responses"),
                          CachedQueryEmbedder(_Offline(), FIXTURES / "embeddings"))
    return judges.judge(JudgeInput(fixture["question"], class_, answer, chunks), run=1)


def test_an_answered_lookup_replays_all_three_answer_metrics():
    scores = _judge("answered_q0072", "lookup")
    assert scores.errors == {}
    assert scores.scores == pytest.approx(
        {"faithfulness": 0.8, "answer_relevancy": 0.9548, "citation_support": 1.0}, abs=1e-4)
    assert scores.calls == scores.replayed == 6


@pytest.mark.parametrize(("name", "class_", "metric"), [
    ("declined_q0005", "decline", "decline_correct"),
    ("not_found_q0011", "unanswerable", "not_found_correct"),
])
def test_decline_and_not_found_verdicts_replay(name, class_, metric):
    scores = _judge(name, class_)
    assert scores.scores == {metric: 1.0}
    assert scores.calls == scores.replayed == 1


def test_recorded_judge_responses_are_luna_at_medium_effort_with_no_secrets():
    files = list((FIXTURES / "responses").rglob("*.json"))
    assert len(files) == 8
    assert {f.parent.relative_to(FIXTURES / "responses").as_posix() for f in files} == {
        "gpt-6-luna/medium"}
    for f in files:
        text = f.read_text(encoding="utf-8")
        stored = json.loads(text)
        assert stored["response"]["model"].startswith("gpt-6-luna")
        assert stored["response"]["status"] == "completed"
        assert stored["response"]["store"] is False
        assert not SECRET_PATTERN.search(text)
