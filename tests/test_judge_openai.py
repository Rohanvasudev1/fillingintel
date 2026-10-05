"""The OpenAI judge class that Ragas calls (Step 5, ticket 09).

The backend is scripted with recorded ``gpt-6-luna`` response bodies, so these
tests check the request the judge sends, the cache key that keeps the three
judge runs independent and apart from Anthropic keys, and how unusable replies
are reported.  No test touches the network.
"""
import asyncio
import hashlib
import json

import pytest
from pydantic import BaseModel
from ragas.llms.base import InstructorBaseRagasLLM

from eval.judging.openai_backend import parse_openai_reply
from eval.judging.openai_judge import (
    JUDGE_EFFORT,
    JUDGE_MAX_OUTPUT_TOKENS,
    JUDGE_MODEL,
    JudgeReplyError,
    OpenAIJudge,
    output_schema,
)
from retrieve.answer_model import AnswerModelError, AnswerRequest
from retrieve.pricing import openai_cost
from retrieve.response_cache import CachedAnswerModel
from tests.judge_fakes import ScriptedBackend
from tests.openai_fixtures import RECORDED, incomplete, with_refusal


class Verdict(BaseModel):
    reason: str
    supported: bool


GOOD = {"Verdict": {"reason": "stated in the excerpt", "supported": True}}


def test_the_judge_is_a_ragas_instructor_llm():
    assert isinstance(OpenAIJudge(ScriptedBackend(GOOD), run=1), InstructorBaseRagasLLM)


def test_the_request_asks_luna_for_a_strict_schema_with_effort_and_no_sampling_settings():
    backend = ScriptedBackend(GOOD)
    OpenAIJudge(backend, run=1).generate("Is it supported?", Verdict)
    (request,) = backend.requests
    params = request.params()
    assert params["model"] == JUDGE_MODEL == "gpt-6-luna"
    assert params["reasoning"] == {"effort": JUDGE_EFFORT}
    assert JUDGE_EFFORT == "medium"
    assert params["max_output_tokens"] == JUDGE_MAX_OUTPUT_TOKENS == 25_000
    assert params["store"] is False
    assert params["input"] == [{"role": "user", "content": "Is it supported?"}]
    text_format = params["text"]["format"]
    assert text_format["type"] == "json_schema"
    assert text_format["strict"] is True
    assert text_format["name"] == "Verdict"
    assert text_format["schema"]["additionalProperties"] is False
    assert text_format["schema"]["required"] == ["reason", "supported"]
    assert not {"temperature", "top_p", "top_logprobs", "seed", "verbosity"} & set(params)


def test_reply_fields_keep_their_declared_order_so_reasons_come_first():
    class ReasonLast(BaseModel):
        verdict: int
        reason: str

    backend = ScriptedBackend({"Verdict": GOOD["Verdict"],
                               "ReasonLast": {"verdict": 1, "reason": "r"}})
    judge = OpenAIJudge(backend, run=1)
    judge.generate("p", Verdict)
    judge.generate("p", ReasonLast)
    orders = [list(r.params()["text"]["format"]["schema"]["properties"])
              for r in backend.requests]
    assert orders == [["reason", "supported"], ["verdict", "reason"]]


def test_generate_and_agenerate_return_the_validated_reply():
    judge = OpenAIJudge(ScriptedBackend(GOOD), run=1)
    assert judge.generate("p", Verdict) == Verdict(reason="stated in the excerpt", supported=True)
    reply = asyncio.run(judge.agenerate("q", Verdict))
    assert reply.supported is True


def test_each_run_and_each_repeat_of_a_prompt_has_its_own_cache_key():
    backend = ScriptedBackend(GOOD)
    first, second = OpenAIJudge(backend, run=1), OpenAIJudge(backend, run=2)
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
        OpenAIJudge(backend, run=3).generate("same prompt", Verdict)
    assert backend.requests[0].cache_key() == backend.requests[1].cache_key()


def test_the_cache_key_names_the_provider_and_the_answer_key_is_unchanged():
    backend = ScriptedBackend(GOOD)
    OpenAIJudge(backend, run=1).generate("p", Verdict)
    (request,) = backend.requests
    hashed = {"provider": "openai", "params": request.params(), "run": 1, "repeat": 0}
    canonical = json.dumps(hashed, sort_keys=True, ensure_ascii=False)
    assert request.cache_key() == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    # The Anthropic answer key is still the SHA-256 of its params alone (computed before
    # ticket 09), so cached answers and the recorded answer fixtures stay valid.
    answer = AnswerRequest("claude-sonnet-5-5", "high", 16_000, "system", "user")
    assert answer.cache_key() == (
        "97c6d60402d9e80eb50659194696987919cd02f4a0963add20b1b24a754788a9")


@pytest.mark.parametrize("run", [0, 4])
def test_runs_are_numbered_one_to_three(run):
    with pytest.raises(ValueError, match="run"):
        OpenAIJudge(ScriptedBackend(GOOD), run=run)


@pytest.mark.parametrize(("body", "match"), [
    (with_refusal(RECORDED), "refus"),
    (incomplete(RECORDED), "max_output_tokens"),
])
def test_a_refusal_or_incomplete_reply_is_a_reply_error_and_still_costed(body, match):
    judge = OpenAIJudge(ScriptedBackend(GOOD, body=body), run=1)
    with pytest.raises(JudgeReplyError, match=match):
        judge.generate("p", Verdict)
    assert len(judge.calls) == 1
    assert judge.cost_usd > 0


def test_a_reply_that_does_not_match_the_schema_is_a_reply_error():
    judge = OpenAIJudge(ScriptedBackend(GOOD, text='{"reason": "no verdict"}'), run=1)
    with pytest.raises(JudgeReplyError, match="Verdict"):
        judge.generate("p", Verdict)


def test_the_judge_counts_tokens_and_cost_per_call():
    judge = OpenAIJudge(ScriptedBackend(GOOD), run=1)
    judge.generate("p", Verdict)
    judge.generate("p", Verdict)
    usage = parse_openai_reply(RECORDED).usage
    assert [c.usage for c in judge.calls] == [usage, usage]
    assert judge.cost_usd == pytest.approx(2 * openai_cost(JUDGE_MODEL, usage))
    assert judge.replayed == 0


def test_judge_replies_go_through_the_response_cache(tmp_path):
    backend = ScriptedBackend(GOOD)
    cached = CachedAnswerModel(backend, cache_dir=tmp_path)
    OpenAIJudge(cached, run=1).generate("p", Verdict)
    replay = OpenAIJudge(cached, run=1)
    assert replay.generate("p", Verdict).supported is True
    assert len(backend.requests) == 1
    assert replay.replayed == 1
    (stored,) = tmp_path.glob("gpt-6-luna/medium/*.json")
    assert stored.stem == backend.requests[0].cache_key()
    OpenAIJudge(cached, run=2).generate("p", Verdict)  # another run is a new call
    assert len(backend.requests) == 2


@pytest.mark.parametrize("status", ["failed", "cancelled", "in_progress", "queued"])
def test_a_failed_or_unfinished_response_stops_the_run_instead_of_one_metric(status):
    judge = OpenAIJudge(ScriptedBackend(GOOD, body={**RECORDED, "status": status}), run=1)
    with pytest.raises(AnswerModelError, match=status):
        judge.generate("p", Verdict)


def test_the_strict_schema_is_pinned_so_an_sdk_upgrade_that_changes_it_fails_here():
    # output_schema uses the SDK's private strict transform; openai==3.3.0 is pinned for it.
    assert json.loads(output_schema(Verdict)) == {
        "title": "Verdict",
        "type": "object",
        "properties": {"reason": {"title": "Reason", "type": "string"},
                       "supported": {"title": "Supported", "type": "boolean"}},
        "required": ["reason", "supported"],
        "additionalProperties": False,
    }
