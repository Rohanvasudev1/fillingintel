"""The judge model that Ragas and the project's own judges call (Step 5, ticket 07).

Ragas's documented Anthropic route sends ``temperature``, ``top_p`` and a forced
``tool_choice``, which ``claude-opus-5-5`` rejects.  ``ClaudeJudge`` subclasses
Ragas's ``InstructorBaseRagasLLM`` instead and asks for structured output through
``output_config.format``, next to the effort, with no sampling settings.

Every request goes through the response cache.  A judge request's cache key
covers the run number (1 to ``JUDGE_RUNS``), so the three runs are independent
calls, and a repeat counter, so a prompt sent twice within one run (Ragas answer
relevancy sends its prompt ``strictness`` times) gets two answers.  The counter
starts at zero for each new judge, so a rerun replays the same keys.

A reply the judge cannot use (a refusal, a truncated reply, or JSON that does not
match the schema) raises ``JudgeReplyError``, which the scorer records against
that one metric.  An API or cache failure propagates and stops the run.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError
from ragas.llms.base import InstructorBaseRagasLLM

from retrieve.answer_model import AnswerModel, Effort, TokenUsage, parse_reply
from retrieve.pricing import anthropic_cost

JUDGE_MODEL = "claude-opus-5-5"
JUDGE_EFFORT: Effort = "medium"
JUDGE_MAX_TOKENS = 16_000  # thinking plus text, as for the answer model
JUDGE_RUNS = 3
_UNUSABLE_STOPS = ("refusal", "max_tokens")

ReplyT = TypeVar("ReplyT", bound=BaseModel)


class JudgeReplyError(RuntimeError):
    """The judge replied, but the reply cannot be scored."""


def output_schema(response_model: type[BaseModel]) -> str:
    """The JSON schema the API is asked to follow, in the SDK's strict form, as JSON.

    Properties keep the model's declared order, because the judge writes fields in
    schema order and a reason should come before its verdict.  The cache key hashes
    the request with sorted keys, so the order does not affect caching.
    """
    return json.dumps(anthropic.transform_schema(response_model.model_json_schema()))


@dataclass(frozen=True)
class JudgeRequest:
    """One judge call.  ``run`` and ``repeat`` go into the cache key, never to the API."""

    model: str
    effort: Effort
    max_tokens: int
    prompt: str
    schema: str  # from ``output_schema``
    run: int
    repeat: int

    def params(self) -> dict[str, object]:
        """The keyword arguments for ``messages.create``."""
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": self.prompt}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": json.loads(self.schema)},
            },
        }

    def cache_key(self) -> str:
        canonical = json.dumps({"params": self.params(), "run": self.run, "repeat": self.repeat},
                               sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class JudgeCall:
    usage: TokenUsage
    cost_usd: float
    from_cache: bool


class ClaudeJudge(InstructorBaseRagasLLM):
    """One run of the judge, for one question.  Create a new one per question and run.

    Ragas calls ``agenerate`` and reads only the parsed reply, so the repeat
    counter and the call log live on this short-lived object; each is replaced
    with a new copy on every call, never changed in place.
    """

    def __init__(self, backend: AnswerModel, run: int, model: str = JUDGE_MODEL,
                 effort: Effort = JUDGE_EFFORT, max_tokens: int = JUDGE_MAX_TOKENS):
        if not 1 <= run <= JUDGE_RUNS:
            raise ValueError(f"judge run must be 1 to {JUDGE_RUNS}, got {run}")
        self._backend = backend
        self._run = run
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens
        self._repeats: dict[str, int] = {}
        self.calls: tuple[JudgeCall, ...] = ()

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def replayed(self) -> int:
        return sum(c.from_cache for c in self.calls)

    def _request(self, prompt: str, response_model: type[BaseModel]) -> JudgeRequest:
        schema = output_schema(response_model)
        seen = hashlib.sha256(f"{schema}\n{prompt}".encode()).hexdigest()
        repeat = self._repeats.get(seen, 0)
        self._repeats = {**self._repeats, seen: repeat + 1}
        return JudgeRequest(self._model, self._effort, self._max_tokens, prompt, schema,
                            self._run, repeat)

    def generate(self, prompt: str, response_model: type[ReplyT]) -> ReplyT:
        """Ask the judge *prompt*; the reply validated as *response_model*."""
        response = self._backend.complete(self._request(prompt, response_model))
        reply = parse_reply(response.body)
        call = JudgeCall(reply.usage, anthropic_cost(self._model, reply.usage),
                         response.from_cache)
        self.calls = (*self.calls, call)  # tokens are spent even when the reply is unusable
        if reply.stop_reason in _UNUSABLE_STOPS:
            raise JudgeReplyError(f"judge stopped with {reply.stop_reason}")
        try:
            return response_model.model_validate_json(reply.text)
        except ValidationError as exc:
            raise JudgeReplyError(
                f"judge reply does not match {response_model.__name__}: "
                f"{exc.error_count()} errors"
            ) from exc

    async def agenerate(self, prompt: str, response_model: type[ReplyT]) -> ReplyT:
        """Ragas's metrics await this; the call itself is synchronous."""
        return self.generate(prompt, response_model)
