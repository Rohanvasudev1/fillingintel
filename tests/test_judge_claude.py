"""The Claude judge class that Ragas calls (Step 5, ticket 07).

The backend is scripted, so these tests check the request the judge sends, the
cache key that keeps the three judge runs independent, and how unusable replies
are reported.  No test touches the network.
"""
import asyncio

import pytest
from pydantic import BaseModel
from ragas.llms.base import InstructorBaseRagasLLM

from eval.judging.claude import (
    JUDGE_EFFORT,
    JUDGE_MODEL,
    ClaudeJudge,
    JudgeReplyError,
)
from retrieve.answer_model import parse_reply
from retrieve.pricing import anthropic_cost
from retrieve.response_cache import CachedAnswerModel
from tests.judge_fakes import RECORDED, ScriptedBackend


class Verdict(BaseModel):
    reason: str
    supported: bool


GOOD = {"Verdict": {"reason": "stated in the excerpt", "supported": True}}


def test_the_judge_is_a_ragas_instructor_llm():
    assert isinstance(ClaudeJudge(ScriptedBackend(GOOD), run=1), InstructorBaseRagasLLM)


def test_the_request_asks_opus_at_medium_effort_for_the_schema_with_no_sampling_settings():
    backend = ScriptedBackend(GOOD)
    ClaudeJudge(backend, run=1).generate("Is it supported?", Verdict)
    (request,) = backend.requests
    params = request.params()
    assert params["model"] == JUDGE_MODEL == "claude-opus-5-5"
    assert params["output_config"]["effort"] == JUDGE_EFFORT == "medium"
    output_format = params["output_config"]["format"]
    assert output_format["type"] == "json_schema"
    assert list(output_format["schema"]["properties"]) == ["reason", "supported"]  # as declared
    assert output_format["schema"]["additionalProperties"] is False
    assert params["messages"] == [{"role": "user", "content": "Is it supported?"}]
    assert not {"temperature", "top_p", "top_k", "tool_choice", "thinking"} & set(params)


def test_generate_and_agenerate_return_the_validated_reply():
    judge = ClaudeJudge(ScriptedBackend(GOOD), run=1)
    assert judge.generate("p", Verdict) == Verdict(reason="stated in the excerpt", supported=True)
    reply = asyncio.run(judge.agenerate("q", Verdict))
    assert reply.supported is True


def test_each_run_and_each_repeat_of_a_prompt_has_its_own_cache_key():
    backend = ScriptedBackend(GOOD)
    first, second = ClaudeJudge(backend, run=1), ClaudeJudge(backend, run=2)
    first.generate("same prompt", Verdict)
    first.generate("same prompt", Verdict)  # Ragas answer relevancy repeats its prompt
    second.generate("same prompt", Verdict)
    keys = [r.cache_key() for r in backend.requests]
    assert len(set(keys)) == 3
    assert [(r.run, r.repeat) for r in backend.requests] == [(1, 0), (1, 1), (2, 0)]
    # The API sees the same request each time; only the cache key differs.
    assert len({str(r.params()) for r in backend.requests}) == 1


def test_a_new_judge_for_the_same_run_reuses_the_same_keys():
    backend = ScriptedBackend(GOOD)
    for _ in range(2):
        ClaudeJudge(backend, run=3).generate("same prompt", Verdict)
    assert backend.requests[0].cache_key() == backend.requests[1].cache_key()


@pytest.mark.parametrize("run", [0, 4])
def test_runs_are_numbered_one_to_three(run):
    with pytest.raises(ValueError, match="run"):
        ClaudeJudge(ScriptedBackend(GOOD), run=run)


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_a_refusal_or_truncated_reply_is_a_reply_error_and_still_costed(stop_reason):
    judge = ClaudeJudge(ScriptedBackend(GOOD, stop_reason=stop_reason), run=1)
    with pytest.raises(JudgeReplyError, match=stop_reason):
        judge.generate("p", Verdict)
    assert len(judge.calls) == 1


def test_a_reply_that_does_not_match_the_schema_is_a_reply_error():
    judge = ClaudeJudge(ScriptedBackend(GOOD, text='{"reason": "no verdict"}'), run=1)
    with pytest.raises(JudgeReplyError, match="Verdict"):
        judge.generate("p", Verdict)


def test_the_judge_counts_tokens_and_cost_per_call():
    judge = ClaudeJudge(ScriptedBackend(GOOD), run=1)
    judge.generate("p", Verdict)
    judge.generate("p", Verdict)
    usage = parse_reply(RECORDED).usage
    assert [c.usage for c in judge.calls] == [usage, usage]
    assert judge.cost_usd == pytest.approx(2 * anthropic_cost(JUDGE_MODEL, usage))
    assert judge.replayed == 0


def test_judge_replies_go_through_the_response_cache(tmp_path):
    backend = ScriptedBackend(GOOD)
    cached = CachedAnswerModel(backend, cache_dir=tmp_path)
    ClaudeJudge(cached, run=1).generate("p", Verdict)
    replay = ClaudeJudge(cached, run=1)
    assert replay.generate("p", Verdict).supported is True
    assert len(backend.requests) == 1
    assert replay.replayed == 1
    (stored,) = tmp_path.glob("claude-opus-5-5/medium/*.json")
    assert stored.stem == backend.requests[0].cache_key()
    ClaudeJudge(cached, run=2).generate("p", Verdict)  # another run is a new call
    assert len(backend.requests) == 2
