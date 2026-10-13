"""The claude-haiku-5-5 persistence adjudicator: one call per candidate pair (Step 10).

ClaudePersistAdjudicator.judge() builds the request (persist.request), sends it
through the model it was given (the cached Anthropic client in a run) and turns
the reply into a PersistDecision, as resolve.adjudicate does for entities:

- A call that raises AnswerModelError is `call_failed`; a rerun tries again.
- A reply cut off or refused, not JSON, or not the schema's shape is
  `invalid_reply`.
- Each quote is checked against its own factor's sent chunks with Step 8's
  span check (extract.spans.find_span). A quote found only in the other
  factor's chunks is `quote_from_other_factor`; otherwise `quote_not_found`.
- `distinct` and `unsure` are counted by answer. Only `same` with both quotes
  verified links, and the verified spans are the chunk's own text.

The decision ID hashes the pair, the prompt version, the model and the effort.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass

from extract.spans import find_span
from persist.candidates import CandidatePair
from persist.decisions import Answer, Failure, LinkEvidence, PersistDecision
from persist.output_schema import FIELDS
from persist.prompt import PersistPrompt
from persist.request import PersistRequest, build_request, sent_chunks
from resolve.nodes import ResolveNode
from retrieve.answer_model import AnswerModel, AnswerModelError, TokenUsage, parse_reply

logger = logging.getLogger(__name__)
_UNUSABLE_STOPS = frozenset({"max_tokens", "refusal"})
_ANSWER_FAILURES = {Answer.DISTINCT: Failure.DISTINCT, Answer.UNSURE: Failure.UNSURE}


class ReplyError(ValueError):
    """The reply is not the shape the schema asks for."""


@dataclass(frozen=True, slots=True)
class _Reply:
    answer: Answer
    quote_earlier: str
    chunk_id_earlier: str
    quote_later: str
    chunk_id_later: str
    reason: str


class ClaudePersistAdjudicator:
    """Judges candidate pairs through *model*, with *prompt* and the chunk *texts*."""

    def __init__(self, model: AnswerModel, prompt: PersistPrompt, texts: Mapping[str, str]):
        self._model = model
        self._prompt = prompt
        self._texts = texts

    @property
    def prompt_version(self) -> str:
        return self._prompt.prompt_version

    def request(self, later: ResolveNode, earlier: ResolveNode) -> PersistRequest:
        return build_request(self._prompt, later, earlier, self._texts)

    def judge(self, candidate: CandidatePair, later: ResolveNode,
              earlier: ResolveNode) -> PersistDecision:
        """The decision on *candidate*; an error not about this pair (the cache) raises."""
        request = self.request(later, earlier)
        base = _Base(candidate, self._decision_id(candidate, request), self.prompt_version)
        try:
            response = self._model.complete(request)
        except AnswerModelError as exc:
            logger.warning("pair %s / %s: call failed: %s", candidate.later_key,
                           candidate.earlier_key, exc)
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
        return self._checked(base, parsed, later, earlier, reply.usage, response.from_cache)

    def _decision_id(self, candidate: CandidatePair, request: PersistRequest) -> str:
        basis = [candidate.later_key, candidate.earlier_key, self.prompt_version, request.model,
                 request.effort]
        text = json.dumps(basis, separators=(",", ":"), ensure_ascii=False)
        return "p-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def _checked(self, base: _Base, reply: _Reply, later: ResolveNode, earlier: ResolveNode,
                 usage: TokenUsage, from_cache: bool) -> PersistDecision:
        earlier_own, later_own = set(sent_chunks(earlier)), set(sent_chunks(later))
        span_earlier, earlier_failure = self._verify(
            reply.quote_earlier, reply.chunk_id_earlier, earlier_own, later_own - earlier_own)
        span_later, later_failure = self._verify(
            reply.quote_later, reply.chunk_id_later, later_own, earlier_own - later_own)
        failure = earlier_failure or later_failure if reply.answer is Answer.SAME \
            else _ANSWER_FAILURES[reply.answer]
        verified = failure is None and span_earlier is not None and span_later is not None
        return PersistDecision(
            decision_id=base.decision_id, later_key=base.candidate.later_key,
            earlier_key=base.candidate.earlier_key, rank=base.candidate.rank,
            answer=reply.answer, quote_earlier=reply.quote_earlier,
            chunk_id_earlier=reply.chunk_id_earlier, quote_later=reply.quote_later,
            chunk_id_later=reply.chunk_id_later,
            verified_earlier=LinkEvidence(reply.chunk_id_earlier, span_earlier)
            if verified and span_earlier is not None else None,
            verified_later=LinkEvidence(reply.chunk_id_later, span_later)
            if verified and span_later is not None else None,
            reason=reply.reason,
            failure=failure, prompt_version=base.prompt_version, usage=usage,
            from_cache=from_cache,
        )

    def _verify(self, quote: str, chunk_id: str, own: set[str],
                others: set[str]) -> tuple[str | None, Failure | None]:
        """The chunk's own text for *quote* and no failure, or None and the failure."""
        if chunk_id in own and (span := find_span(quote, self._texts[chunk_id])) is not None:
            return span, None
        if chunk_id in others or any(find_span(quote, self._texts[c]) is not None for c in others):
            return None, Failure.QUOTE_FROM_OTHER_FACTOR
        return None, Failure.QUOTE_NOT_FOUND


@dataclass(frozen=True, slots=True)
class _Base:
    candidate: CandidatePair
    decision_id: str
    prompt_version: str

    def failed(self, failure: Failure, reason: str, usage: TokenUsage | None,
               from_cache: bool) -> PersistDecision:
        return PersistDecision(
            decision_id=self.decision_id, later_key=self.candidate.later_key,
            earlier_key=self.candidate.earlier_key, rank=self.candidate.rank, answer=None,
            quote_earlier=None, chunk_id_earlier=None, quote_later=None, chunk_id_later=None,
            verified_earlier=None, verified_later=None, reason=reason, failure=failure,
            prompt_version=self.prompt_version, usage=usage, from_cache=from_cache,
        )


def _parse(text: str) -> _Reply:
    """The reply's fields; ReplyError when it is not the schema's shape."""
    try:
        data = json.loads(text)
    except ValueError:
        raise ReplyError("the reply is not valid JSON") from None
    if not isinstance(data, dict) or set(data) != set(FIELDS):
        raise ReplyError("the reply does not have exactly the schema's fields")
    if not all(isinstance(data[f], str) for f in FIELDS):
        raise ReplyError("a field is not a string")
    try:
        answer = Answer(data["answer"].casefold())  # the API may change case
    except ValueError:
        raise ReplyError(f"answer {data['answer']!r} is not one of "
                         f"{[a.value for a in Answer]}") from None
    return _Reply(answer, data["quote_earlier"], data["chunk_id_earlier"], data["quote_later"],
                  data["chunk_id_later"], data["reason"])
