"""The claude-sonnet-5-5 adjudicator: one call per candidate pair, its reply checked (Step 9).

ClaudeAdjudicator.judge() builds the request (resolve.request), sends it
through the model it was given (the cached, traced Anthropic client in a run),
and turns the reply into a PairDecision:

- A call that raises AnswerModelError is `call_failed`; a rerun tries again.
- A reply cut off or refused, not JSON, or not the schema's shape is
  `invalid_reply`. So is a `related` answer with no kind, or a kind on any
  other answer.
- Each quote is checked against its own node's chunks with Step 8's span check
  (extract.spans.find_span). It must be found in the chunk it names, and that
  chunk must be one sent for its node. A quote that is instead in a chunk only
  the other node cites is `quote_from_other_node`; otherwise `quote_not_found`.
- `unsure`, `distinct` and `related:{kind}` are counted by answer. Only `same`
  with both quotes verified merges.

The decision ID hashes the pair, the prompt version, the model and the effort,
so the same decision made again keeps its ID and a rerun appends nothing.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeVar

from extract.spans import find_span
from resolve.candidates import ScoredPair
from resolve.decisions import Answer, Failure, PairDecision, RelatedKind, related_failure
from resolve.nodes import ResolveNode
from resolve.prompt import ResolvePrompt
from resolve.request import AdjudicateRequest, build_request, sent_chunks
from retrieve.answer_model import AnswerModel, AnswerModelError, TokenUsage, parse_reply

logger = logging.getLogger(__name__)
_UNUSABLE_STOPS = frozenset({"max_tokens", "refusal"})
_FIELDS = frozenset({"answer", "related_kind", "quote_a", "chunk_id_a", "quote_b", "chunk_id_b",
                     "reason"})
_TEXT_FIELDS = ("quote_a", "chunk_id_a", "quote_b", "chunk_id_b", "reason")
_E = TypeVar("_E", Answer, RelatedKind)


class ReplyError(ValueError):
    """The reply is not the shape the schema asks for."""


@dataclass(frozen=True, slots=True)
class _Reply:
    answer: Answer
    related_kind: RelatedKind | None
    quote_a: str
    chunk_id_a: str
    quote_b: str
    chunk_id_b: str
    reason: str


class ClaudeAdjudicator:
    """Judges candidate pairs through *model*, with *prompt* and the chunk *texts*."""

    def __init__(self, model: AnswerModel, prompt: ResolvePrompt, texts: Mapping[str, str]):
        self._model = model
        self._prompt = prompt
        self._texts = texts

    @property
    def prompt_version(self) -> str:
        return self._prompt.prompt_version

    def request(self, a: ResolveNode, b: ResolveNode) -> AdjudicateRequest:
        return build_request(self._prompt, a, b, self._texts)

    def judge(self, pair: ScoredPair, a: ResolveNode, b: ResolveNode) -> PairDecision:
        """The decision on *pair*; an error not about this pair (the cache, a file) raises."""
        request = self.request(a, b)
        base = _Base(pair, self._decision_id(pair, request), self.prompt_version)
        try:
            response = self._model.complete(request)
        except AnswerModelError as exc:
            logger.warning("pair %s / %s: call failed: %s", pair.key_a, pair.key_b, exc)
            return base.failed(Failure.CALL_FAILED, str(exc), None, False)
        try:
            reply = parse_reply(response.body)
        except AnswerModelError as exc:
            return base.failed(Failure.INVALID_REPLY, str(exc), None, response.from_cache)
        if reply.stop_reason in _UNUSABLE_STOPS:
            return base.failed(Failure.INVALID_REPLY, f"the reply stopped with "
                               f"{reply.stop_reason}", reply.usage, response.from_cache)
        try:
            parsed = _parse(reply.text)
        except ReplyError as exc:
            return base.failed(Failure.INVALID_REPLY, str(exc), reply.usage, response.from_cache)
        return self._checked(base, parsed, a, b, reply.usage, response.from_cache)

    def _decision_id(self, pair: ScoredPair, request: AdjudicateRequest) -> str:
        basis = [pair.key_a, pair.key_b, self.prompt_version, request.model, request.effort]
        text = json.dumps(basis, separators=(",", ":"), ensure_ascii=False)
        return "d-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def _checked(self, base: _Base, reply: _Reply, a: ResolveNode, b: ResolveNode,
                 usage: TokenUsage, from_cache: bool) -> PairDecision:
        a_own, b_own = set(sent_chunks(a)), set(sent_chunks(b))
        quote_failures = [f for f in (
            self._quote_failure(reply.quote_a, reply.chunk_id_a, a_own, b_own - a_own),
            self._quote_failure(reply.quote_b, reply.chunk_id_b, b_own, a_own - b_own),
        ) if f is not None]
        verified = not quote_failures
        if reply.answer is Answer.SAME:
            failure = quote_failures[0].value if quote_failures else None
        elif reply.answer is Answer.RELATED and reply.related_kind is not None:
            failure = related_failure(reply.related_kind)
        else:
            failure = reply.answer.value
        return PairDecision(
            decision_id=base.decision_id, key_a=base.pair.key_a, key_b=base.pair.key_b,
            label=base.pair.label, answer=reply.answer, related_kind=reply.related_kind,
            quote_a=reply.quote_a, chunk_id_a=reply.chunk_id_a, quote_b=reply.quote_b,
            chunk_id_b=reply.chunk_id_b, reason=reply.reason, quotes_verified=verified,
            failure=failure, prompt_version=base.prompt_version, usage=usage,
            from_cache=from_cache,
        )

    def _quote_failure(self, quote: str, chunk_id: str, own: set[str],
                       others: set[str]) -> Failure | None:
        if chunk_id in own and find_span(quote, self._texts[chunk_id]) is not None:
            return None
        if chunk_id in others or any(find_span(quote, self._texts[c]) is not None for c in others):
            return Failure.QUOTE_FROM_OTHER_NODE
        return Failure.QUOTE_NOT_FOUND


@dataclass(frozen=True, slots=True)
class _Base:
    pair: ScoredPair
    decision_id: str
    prompt_version: str

    def failed(self, failure: Failure, reason: str, usage: TokenUsage | None,
               from_cache: bool) -> PairDecision:
        return PairDecision(
            decision_id=self.decision_id, key_a=self.pair.key_a, key_b=self.pair.key_b,
            label=self.pair.label, answer=None, related_kind=None, quote_a=None,
            chunk_id_a=None, quote_b=None, chunk_id_b=None, reason=reason,
            quotes_verified=False, failure=failure.value, prompt_version=self.prompt_version,
            usage=usage, from_cache=from_cache,
        )


def _parse(text: str) -> _Reply:
    """The reply's fields; ReplyError when it is not the schema's shape."""
    try:
        data = json.loads(text)
    except ValueError:
        raise ReplyError("the reply is not valid JSON") from None
    if not isinstance(data, dict) or set(data) != _FIELDS:
        raise ReplyError("the reply does not have exactly the schema's fields")
    if not all(isinstance(data[f], str) for f in _TEXT_FIELDS):
        raise ReplyError("a quote, chunk ID or reason is not a string")
    answer = _enum(Answer, data["answer"], "answer")
    kind = None if data["related_kind"] is None else _enum(RelatedKind, data["related_kind"],
                                                           "related_kind")
    if (answer is Answer.RELATED) != (kind is not None):
        raise ReplyError("related_kind is given exactly when the answer is related")
    return _Reply(answer, kind, data["quote_a"], data["chunk_id_a"], data["quote_b"],
                  data["chunk_id_b"], data["reason"])


def _enum(cls: type[_E], value: object, field: str) -> _E:
    """*value* as a member of *cls*, compared case-insensitively (the API may change case)."""
    if isinstance(value, str):
        try:
            return cls(value.casefold())
        except ValueError:
            pass
    raise ReplyError(f"{field} {value!r} is not one of {[m.value for m in cls]}")
