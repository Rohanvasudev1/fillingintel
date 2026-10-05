"""Write a cited answer from retrieved chunks (Step 5).

The answer model sees every retrieved chunk and replies with a status line
(``answered``, ``declined`` or ``not_found``, as the prompt asks) and then
sentences that cite chunk IDs.  Citation enforcement keeps only sentences whose
citations were all retrieved (invariant 3).  A decline or a not-found answer is
shown as a fixed sentence that makes no claim about the filings, followed by any
cited sentences that survived.  An API refusal is recorded, with no answer text.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

from retrieve.answer_model import (
    ANSWER_EFFORT,
    ANSWER_MAX_TOKENS,
    ANSWER_MODEL,
    AnswerModel,
    AnswerRequest,
    TokenUsage,
    parse_reply,
)
from retrieve.answer_prompt import AnswerPrompt, SourceChunk, render_user
from retrieve.citations import CitationCheck, enforce_citations
from retrieve.pricing import anthropic_cost

DECLINE_TEXT = (
    "FilingIntel answers questions about what the filings say and does not give "
    "investment advice."
)
NOT_FOUND_TEXT = (
    "The retrieved filings do not contain the information needed to answer this question."
)
_STATUS_LINE = re.compile(r"\A\s*STATUS:[ \t]*(answered|declined|not_found)[ \t]*(?:\n|\Z)",
                          re.IGNORECASE)
_LEAD_TEXT = {"declined": DECLINE_TEXT, "not_found": NOT_FOUND_TEXT}

AnswerStatus = Literal["answered", "declined", "not_found", "no_status", "refused"]


@dataclass(frozen=True)
class Answer:
    """One generated answer after citation enforcement, with its cost and latency."""

    status: AnswerStatus
    text: str  # what a reader sees
    raw_text: str  # the model's reply, before enforcement
    citation_check: CitationCheck
    model: str  # as the response reports it
    message_id: str
    stop_reason: str | None
    refusal_category: str | None
    usage: TokenUsage
    cost_usd: float  # from the reported tokens, whether or not this run replayed it
    generation_ms: float  # the API call's latency when it was made
    from_cache: bool


def _read_status(text: str) -> tuple[AnswerStatus, str]:
    """The status line's value and the text after it; ``no_status`` if it is missing."""
    match = _STATUS_LINE.match(text)
    if match is None:
        return "no_status", text
    return cast(AnswerStatus, match.group(1).lower()), text[match.end():]


def _shown_text(status: AnswerStatus, check: CitationCheck) -> str:
    lead = _LEAD_TEXT.get(status)
    return " ".join(part for part in (lead, check.text) if part)


def write_answer(
    question: str,
    chunks: Sequence[SourceChunk],
    model: AnswerModel,
    prompt: AnswerPrompt,
) -> Answer:
    """Ask *model* to answer *question* from all of *chunks*, then enforce citations."""
    request = AnswerRequest(model=ANSWER_MODEL, effort=ANSWER_EFFORT,
                            max_tokens=ANSWER_MAX_TOKENS, system=prompt.system,
                            user=render_user(prompt, question, chunks))
    response = model.complete(request)
    reply = parse_reply(response.body)
    status, body = ("refused", "") if reply.stop_reason == "refusal" else _read_status(reply.text)
    check = enforce_citations(body, {c.chunk_id for c in chunks})
    return Answer(
        status=status,
        text=_shown_text(status, check),  # empty for a refusal: no lead text, nothing kept
        raw_text=reply.text,
        citation_check=check,
        model=reply.model,
        message_id=reply.message_id,
        stop_reason=reply.stop_reason,
        refusal_category=reply.refusal_category,
        usage=reply.usage,
        cost_usd=anthropic_cost(ANSWER_MODEL, reply.usage),
        generation_ms=response.api_ms,
        from_cache=response.from_cache,
    )
