"""``python -m eval.spotcheck`` with a fake arm and fake judges (Step 5, ticket 10).

The questions are the real agent-drafted dev records.  The fake arm answers
every question with two cited sentences, from the response cache; each fake
judge scores by its model and effort, so the comparison is known in advance.
"""
import json
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from types import MappingProxyType

import pytest

from eval.judging.runner import JudgeRunError
from eval.judging.scoring import JudgeConfig, JudgeInput, RunScores, metrics_for
from eval.question_sets import agent_drafted_set, load_question_sets
from eval.spotcheck import CANDIDATES, EXIT_RUN_ERROR, EXIT_USAGE, REFERENCE, judge_name, main
from retrieve.answer import write_answer
from retrieve.answer_model import ApiResponse, parse_reply
from retrieve.answer_prompt import SourceChunk, load_prompt
from retrieve.arm import ArmConfig, ArmResult, ArmSpec, RetrievedChunk
from retrieve.pricing import anthropic_cost
from retrieve.question_filter import QuestionFilter
from tests.anthropic_fixtures import load, with_text

TODAY = date(2026, 10, 5)
CACHE = Path("/nonexistent/responses")  # the fakes never touch it
RECORDED = load("answered_q0072")["response"]
CHUNKS = tuple(SourceChunk(f"0001045810-26-000075:{i:04d}", "NVDA", "10-K", "FY2026", "item_7",
                           "text") for i in range(10))
DEV = {r.question: r for r in agent_drafted_set(load_question_sets()).records
       if r.split == "dev"}


class _Replay:
    def __init__(self, from_cache: bool):
        self._from_cache = from_cache

    def complete(self, request) -> ApiResponse:
        text = f"STATUS: answered\nA rose [{CHUNKS[0].chunk_id}]. B fell [{CHUNKS[1].chunk_id}]."
        return ApiResponse(with_text(RECORDED, text), 100.0, from_cache=self._from_cache)


class FakeArm:
    name = "fake"
    config = ArmConfig(models=MappingProxyType({"answer": "claude-sonnet-5-5"}), k=10,
                       chunker_version="1", efforts=MappingProxyType({"answer": "high"}),
                       answer_prompt=load_prompt("v1"), answer_max_tokens=16_000)

    def __init__(self, from_cache: bool = True):
        self.questions: list[str] = []
        self._from_cache = from_cache

    def run(self, question: str) -> ArmResult:
        self.questions.append(question)
        return ArmResult(
            retrieved=tuple(RetrievedChunk(c.chunk_id, 0.9) for c in CHUNKS),
            question_filter=QuestionFilter((), (), (), ()), companies_without_chunks=(),
            embed_ms=1.0, search_ms=1.0, query_cached=True, embed_tokens=5, embed_cost_usd=0.0,
            answer=write_answer(question, CHUNKS, _Replay(self._from_cache), load_prompt("v1")),
            sources=CHUNKS)


class FakeJudge:
    """Scores 0.8 everywhere, except that an effort in *off* gives faithfulness 0.5."""

    def __init__(self, config: JudgeConfig, off: frozenset[str], seen: list):
        self.config = config
        self._off = off
        self._seen = seen

    def judge(self, item: JudgeInput, run: int) -> RunScores:
        self._seen.append((self.config.model, self.config.effort, item.answer.text, run))
        name = judge_name(self.config)
        scores = {m: 0.8 for m in metrics_for(item.class_)}
        verdicts = {m: (True,) for m in metrics_for(item.class_) if m.endswith("_correct")}
        if "citation_support" in scores:
            verdicts["citation_support"] = (True, True)
            scores["faithfulness"] = 0.5 if name in self._off else 0.8
        return RunScores(run, MappingProxyType(scores), MappingProxyType({}), 0.001, 2, 0,
                         verdicts=MappingProxyType(verdicts))


@pytest.fixture
def env(monkeypatch):
    for name in ("OPENAI_API_KEY", "VOYAGE_API_KEY"):
        monkeypatch.setenv(name, "not-a-real-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


def _run(tmp_path, off=frozenset(), error=None, runs=None, from_cache=True):
    arm, opened, seen = FakeArm(from_cache), [], []

    def open_judges(cache: Path, config: JudgeConfig):
        opened.append((cache, config))
        if error is not None:
            raise error
        return nullcontext(FakeJudge(config, off, seen))

    argv = [] if runs is None else ["--runs", str(runs)]
    code = main(argv, arm_spec=ArmSpec("fake", (), lambda cache: nullcontext(arm)),
                open_judges_=open_judges, runs_dir=tmp_path, today=TODAY,
                response_cache=CACHE)
    return code, arm, opened, seen


def _document(tmp_path) -> dict:
    (path,) = tmp_path.glob("2026-10-05-spotcheck-dev-*.json")
    return json.loads(path.read_text())


def test_the_check_answers_once_and_every_judge_scores_the_same_answers(tmp_path, env):
    code, arm, opened, seen = _run(tmp_path)
    assert code == 0
    assert len(arm.questions) == 10 and all(q in DEV for q in arm.questions)
    assert [(c.model, c.effort, c.runs) for _, c in opened] == [
        ("gpt-6-luna", "medium", 3), ("gpt-6-luna", "high", 3), ("gpt-6-sol", "medium", 3)]
    assert {cache for cache, _ in opened} == {CACHE}
    assert len(seen) == 90 and len({text for *_, text, _ in seen}) == 1
    document = _document(tmp_path)
    assert document["header"]["label"] == "spot check, not calibration"
    assert document["header"]["thresholds"] == {"min_agreement": 0.85, "max_mean_gap": 0.10,
                                                "max_missing_share": 0.15}
    assert document["answers"]["replayed"] == 10 and document["answers"]["new"] == 0
    assert document["choice"] == "gpt-6-luna/medium"
    assert all(c["passed"] for c in document["comparisons"])


def test_answers_not_in_the_cache_are_counted_as_new_with_their_cost(tmp_path, env):
    _run(tmp_path, from_cache=False)
    answers = _document(tmp_path)["answers"]
    assert (answers["replayed"], answers["new"]) == (0, 10)
    cost = anthropic_cost("claude-sonnet-5-5", parse_reply(RECORDED).usage)
    assert answers["cost_usd"] == pytest.approx(10 * cost)


def test_medium_failing_one_metric_hands_the_baseline_to_high(tmp_path, env):
    _run(tmp_path, off=frozenset({"gpt-6-luna/medium"}))
    document = _document(tmp_path)
    medium = document["comparisons"][0]
    assert not medium["passed"]
    failed = [m["metric"] for m in medium["metrics"] if not m["passed"]]
    assert failed == ["faithfulness"]
    assert document["choice"] == "gpt-6-luna/high"


def test_both_efforts_failing_hands_the_baseline_to_sol(tmp_path, env):
    _run(tmp_path, off=frozenset({"gpt-6-luna/medium", "gpt-6-luna/high"}))
    assert _document(tmp_path)["choice"] == "gpt-6-sol/medium"


def test_runs_sets_how_often_each_judge_scores_a_question(tmp_path, env):
    _, _, opened, seen = _run(tmp_path, runs=1)
    assert {c.runs for _, c in opened} == {1}
    assert {run for *_, run in seen} == {1}


def test_a_judge_failure_stops_the_check_without_a_results_file(tmp_path, env, capsys):
    code, *_ = _run(tmp_path, error=JudgeRunError("judging failed (APIError): boom"))
    assert code == EXIT_RUN_ERROR
    assert "no results written" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.json"))


def test_a_missing_key_stops_the_check_before_any_call(tmp_path, env, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY")
    code, arm, opened, _ = _run(tmp_path)
    assert code == EXIT_USAGE
    assert "OPENAI_API_KEY" in capsys.readouterr().err
    assert arm.questions == [] and opened == []


def test_the_candidates_and_reference_are_the_ones_the_spec_names():
    assert [(c.model, c.effort) for c in CANDIDATES] == [("gpt-6-luna", "medium"),
                                                          ("gpt-6-luna", "high")]
    assert (REFERENCE.model, REFERENCE.effort) == ("gpt-6-sol", "medium")
