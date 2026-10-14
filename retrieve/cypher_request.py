"""The Cypher generation and repair calls, as ``ModelRequest``s (Step 11).

claude-sonnet-5-5 at effort ``high`` with structured output
``{reasoning, cypher}``, no sampling settings and no tools.  ``max_tokens``
covers thinking plus the reply, as for the answer model.  The system prompt is
one block with the only cache breakpoint, since it is identical on every call.
A repair resends the first exchange, then the failed query and the guard's
message, once.  The cache key hashes the whole request, so a prompt, filing or
model change misses the response cache by construction.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from string import Template
from typing import Any, Literal

from retrieve.answer_model import Effort, ModelReply, TokenUsage, parse_reply
from retrieve.cypher_prompt import CypherPrompt, output_schema
from retrieve.question_filter import QuestionFilter

CYPHER_MODEL = "claude-sonnet-5-5"
CYPHER_EFFORT: Effort = "high"
CYPHER_MAX_TOKENS = 16_000
_COMPLETE = "end_turn"

Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class CypherRequest:
    """Everything sent for one generation or repair call."""

    system: str
    messages: tuple[tuple[Role, str], ...]
    model: str = CYPHER_MODEL
    effort: Effort = CYPHER_EFFORT
    max_tokens: int = CYPHER_MAX_TOKENS

    def params(self) -> dict[str, object]:
        """The keyword arguments for ``messages.create``."""
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [
                {"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}},
            ],
            "messages": [{"role": role, "content": text} for role, text in self.messages],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": output_schema()},
            },
        }

    def cache_key(self) -> str:
        """SHA-256 of the full request."""
        canonical = json.dumps(self.params(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def last_user(self) -> str:
        return self.messages[-1][1]


@dataclass(frozen=True)
class CypherReply:
    """A generation or repair reply.  With no usable query, ``cypher`` is None and
    ``error`` says why, written for the repair prompt; the tokens were still spent."""

    reasoning: str
    cypher: str | None
    error: str | None
    text: str  # the reply's text blocks, as sent back in a repair
    usage: TokenUsage


def render_filter(question_filter: QuestionFilter) -> str:
    """The parsed filter as one line; ``none`` when every filing is in scope."""
    if question_filter.accession_nos is None:
        return "none; every filing is in scope"
    parts = (("companies", question_filter.companies), ("periods", question_filter.periods),
             ("forms", question_filter.forms), ("filings", question_filter.accession_nos))
    return "; ".join(f"{name} {', '.join(values) if values else 'any'}" for name, values in parts)


def first_request(prompt: CypherPrompt, question: str,
                  question_filter: QuestionFilter) -> CypherRequest:
    user = Template(prompt.user_template).substitute(question=question,
                                                     filter=render_filter(question_filter))
    return CypherRequest(system=prompt.system, messages=(("user", user),))


def repair_request(prompt: CypherPrompt, first: CypherRequest, reply_text: str, query: str,
                   message: str) -> CypherRequest:
    """*first*'s exchange, then the failed *query* and what went wrong with it."""
    repair = Template(prompt.repair_template).substitute(query=query, message=message)
    return CypherRequest(system=first.system,
                         messages=(*first.messages, ("assistant", reply_text), ("user", repair)))


def parse_cypher_reply(body: Mapping[str, Any]) -> CypherReply:
    """The reasoning and query in *body*, or why it holds none.

    A response the API client would not accept raises ``AnswerModelError``.
    """
    reply = parse_reply(body)
    if reply.stop_reason != _COMPLETE:
        return _no_query(reply, f"the reply stopped with {reply.stop_reason!r} before a "
                                "complete query was written")
    try:
        fields = json.loads(reply.text)
    except ValueError:
        return _no_query(reply, "the reply was not the JSON object asked for")
    if not (isinstance(fields, dict) and isinstance(fields.get("reasoning"), str)
            and isinstance(fields.get("cypher"), str)):
        return _no_query(reply, "the reply lacked a reasoning string and a cypher string")
    return CypherReply(fields["reasoning"], fields["cypher"], None, reply.text, reply.usage)


def _no_query(reply: ModelReply, error: str) -> CypherReply:
    return CypherReply("", None, error, reply.text, reply.usage)
