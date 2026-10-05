"""The judge model that Ragas and the project's own judges call (Step 5, ticket 09).

``gpt-6-luna`` judges through OpenAI's Responses API.  Ragas's own OpenAI route
is not used: it misreads model versions and sends sampling settings
(docs/research/openai-luna-judge.md, section 4).  ``OpenAIJudge`` subclasses
Ragas's ``InstructorBaseRagasLLM`` instead and asks for a strict ``json_schema``
text format, next to the reasoning effort, with ``store=false`` and no
``temperature``, ``top_p`` or ``seed``.

Every request goes through the response cache.  A judge request's cache key
names the provider and covers the run number (1 to ``JUDGE_RUNS``), so the
three runs are independent calls, and a repeat counter, so a prompt sent twice
within one run (Ragas answer relevancy sends its prompt ``strictness`` times)
gets two answers.  The counter starts at zero for each new judge, so a rerun
replays the same keys.

A reply the judge cannot use (a refusal, an incomplete reply, or JSON that does
not match the schema) raises ``JudgeReplyError``, which the scorer records
against that one metric.  An API, network or cache failure, or a response that
failed or did not finish on OpenAI's side, raises ``AnswerModelError``, which
stops the run.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.metadata import version
from typing import TypeVar

# A private SDK module: openai==3.3.0 is pinned, and a test pins the schema it produces.
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ValidationError
from ragas.llms.base import InstructorBaseRagasLLM

from eval.judging.openai_backend import OpenAIUsage, parse_openai_reply
from retrieve.answer_model import AnswerModel, AnswerModelError, Effort
from retrieve.pricing import openai_cost

JUDGE_PROVIDER = "openai"
JUDGE_MODEL = "gpt-6-luna"
JUDGE_EFFORT: Effort = "medium"  # chosen by the ticket 10 spot check against gpt-6-sol
JUDGE_MAX_OUTPUT_TOKENS = 10_000  # reasoning plus JSON; ticket 10 spot check: max 2,877 at medium
JUDGE_RUNS = 3
OPENAI_SDK_VERSION = version("openai")

ReplyT = TypeVar("ReplyT", bound=BaseModel)


class JudgeReplyError(RuntimeError):
    """The judge replied, but the reply cannot be scored."""


def output_schema(response_model: type[BaseModel]) -> str:
    """The JSON schema the API is asked to follow, in OpenAI's strict form, as JSON.

    Properties keep the model's declared order, because the judge writes fields in
    schema order and a reason should come before its verdict.  The cache key hashes
    the request with sorted keys, so the order does not affect caching.
    """
    return json.dumps(to_strict_json_schema(response_model))


@dataclass(frozen=True)
class OpenAIJudgeRequest:
    """One judge call.  ``run`` and ``repeat`` go into the cache key, never to the API."""

    model: str
    effort: Effort
    max_output_tokens: int
    prompt: str
    schema: str  # from ``output_schema``
    schema_name: str
    run: int
    repeat: int
    provider: str = JUDGE_PROVIDER

    def params(self) -> dict[str, object]:
        """The keyword arguments for ``responses.create``."""
        return {
            "model": self.model,
            "input": [{"role": "user", "content": self.prompt}],
            "reasoning": {"effort": self.effort},
            "max_output_tokens": self.max_output_tokens,
            "store": False,
            "text": {"format": {"type": "json_schema", "name": self.schema_name,
                                "strict": True, "schema": json.loads(self.schema)}},
        }

    def cache_key(self) -> str:
        canonical = json.dumps({"provider": self.provider, "params": self.params(),
                                "run": self.run, "repeat": self.repeat},
                               sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class JudgeCall:
    usage: OpenAIUsage
    cost_usd: float
    from_cache: bool


class OpenAIJudge(InstructorBaseRagasLLM):
    """One run of the judge, for one question.  Create a new one per question and run.

    Ragas calls ``agenerate`` and reads only the parsed reply, so the repeat
    counter and the call log live on this short-lived object; each is replaced
    with a new copy on every call, never changed in place.
    """

    def __init__(self, backend: AnswerModel, run: int, model: str = JUDGE_MODEL,
                 effort: Effort = JUDGE_EFFORT,
                 max_output_tokens: int = JUDGE_MAX_OUTPUT_TOKENS):
        if not 1 <= run <= JUDGE_RUNS:
            raise ValueError(f"judge run must be 1 to {JUDGE_RUNS}, got {run}")
        self._backend = backend
        self._run = run
        self._model = model
        self._effort = effort
        self._max_output_tokens = max_output_tokens
        self._repeats: dict[str, int] = {}
        self.calls: tuple[JudgeCall, ...] = ()

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def replayed(self) -> int:
        return sum(c.from_cache for c in self.calls)

    def _request(self, prompt: str, response_model: type[BaseModel]) -> OpenAIJudgeRequest:
        schema = output_schema(response_model)
        seen = hashlib.sha256(f"{schema}\n{prompt}".encode()).hexdigest()
        repeat = self._repeats.get(seen, 0)
        self._repeats = {**self._repeats, seen: repeat + 1}
        return OpenAIJudgeRequest(self._model, self._effort, self._max_output_tokens, prompt,
                                  schema, response_model.__name__, self._run, repeat)

    def generate(self, prompt: str, response_model: type[ReplyT]) -> ReplyT:
        """Ask the judge *prompt*; the reply validated as *response_model*."""
        response = self._backend.complete(self._request(prompt, response_model))
        reply = parse_openai_reply(response.body)
        call = JudgeCall(reply.usage, openai_cost(self._model, reply.usage), response.from_cache)
        self.calls = (*self.calls, call)  # tokens are spent even when the reply is unusable
        if reply.refusal is not None:
            raise JudgeReplyError("judge refused")
        if reply.status == "incomplete":
            raise JudgeReplyError(
                f"judge reply incomplete: {reply.incomplete_reason or 'no reason given'}")
        if reply.status != "completed":
            raise AnswerModelError(f"judge response {reply.response_id} is {reply.status}")
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
