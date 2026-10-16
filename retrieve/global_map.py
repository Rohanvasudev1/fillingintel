"""graph_global's map call for one community, and the check on its points (Step 12, ADR-0008).

The call is claude-sonnet-5-5 at effort ``high`` (the answer model, so a
difference from ``vector`` comes from the method) with structured output
``{points: [{text, score}]}``, no sampling settings and no tools; the system
prompt is one block with the only cache breakpoint.  It sees the community's
title and report, with the report's citations removed, since reports are never
cited, and the community's shown chunks as ``answer/v1``-style excerpts.

A point is kept only when it cites at least one chunk, every chunk it cites
was shown to this call, and its score is an integer from 0 to 100.  A reply cut
off, refused or off the schema gives no points; its outcome says which.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from string import Template
from typing import Any

from retrieve.answer_model import (
    ANSWER_EFFORT,
    ANSWER_MAX_TOKENS,
    ANSWER_MODEL,
    Effort,
    TokenUsage,
    parse_reply,
)
from retrieve.answer_prompt import SourceChunk, render_excerpts
from retrieve.arm import MapDropReason, MapOutcome, MapPoint
from retrieve.citations import cited_ids, strip_citations
from retrieve.global_prompts import SCORE_RANGE, GlobalPrompt, map_output_schema

MAP_MODEL = ANSWER_MODEL
MAP_EFFORT: Effort = ANSWER_EFFORT
MAP_MAX_TOKENS = ANSWER_MAX_TOKENS  # thinking plus the reply
_STOPPED: Mapping[str, MapOutcome] = {"max_tokens": "max_tokens", "refusal": "refused"}


@dataclass(frozen=True)
class MapRequest:
    """Everything sent for one community's map call."""

    system: str
    user: str
    model: str = MAP_MODEL
    effort: Effort = MAP_EFFORT
    max_tokens: int = MAP_MAX_TOKENS

    def params(self) -> dict[str, object]:
        """The keyword arguments for ``messages.create``."""
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [
                {"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}},
            ],
            "messages": [{"role": "user", "content": self.user}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": map_output_schema()},
            },
        }

    def cache_key(self) -> str:
        """SHA-256 of the full request."""
        canonical = json.dumps(self.params(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MapReply:
    outcome: MapOutcome
    points: tuple[MapPoint, ...]  # every point, kept or dropped, in reply order
    usage: TokenUsage
    text: str  # the reply's text blocks, for a span with text capture on


def build_map_request(prompt: GlobalPrompt, question: str, title: str, report: str,
                      chunks: Sequence[SourceChunk]) -> MapRequest:
    user = Template(prompt.user_template).substitute(
        title=title, report=strip_citations(report), excerpts=render_excerpts(chunks),
        question=question)
    return MapRequest(system=prompt.system, user=user)


def read_map_reply(body: Mapping[str, Any], shown: Collection[str],
                   community_id: str) -> MapReply:
    """The points in *body*, each checked against the *shown* chunk IDs.

    A response the API client would not accept raises ``AnswerModelError``.
    """
    reply = parse_reply(body)
    stopped = _STOPPED.get(reply.stop_reason or "")
    if stopped is not None:
        return MapReply(stopped, (), reply.usage, reply.text)
    items = _points(reply.text)
    if items is None:
        return MapReply("invalid_reply", (), reply.usage, reply.text)
    return MapReply("ok", tuple(_check(community_id, text, score, shown)
                                for text, score in items), reply.usage, reply.text)


def _points(text: str) -> list[tuple[str, object]] | None:
    """The reply's (text, score) pairs, or None when it is off the schema."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    points = data.get("points") if isinstance(data, dict) else None
    if not isinstance(points, list):
        return None
    pairs = []
    for item in points:
        if not (isinstance(item, dict) and isinstance(item.get("text"), str)):
            return None
        pairs.append((item["text"], item.get("score")))
    return pairs


def _check(community_id: str, text: str, score: object, shown: Collection[str]) -> MapPoint:
    citations = cited_ids(text)
    valid_score = (score if isinstance(score, int) and not isinstance(score, bool)
                   and SCORE_RANGE[0] <= score <= SCORE_RANGE[1] else None)
    return MapPoint(community_id, text, valid_score, citations,
                    _drop_reason(citations, valid_score, shown))


def _drop_reason(citations: tuple[str, ...], score: int | None,
                 shown: Collection[str]) -> MapDropReason | None:
    if score is None:
        return "bad_score"
    if not citations:
        return "no_citation"
    if any(c not in shown for c in citations):
        return "citation_not_shown"
    return None
