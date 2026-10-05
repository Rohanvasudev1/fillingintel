"""Fakes for the judge tests: a scripted Messages API backend and a fixed embedder.

The backend answers each request with a reply chosen by the requested output
schema, wrapped in a real recorded response body, so ``parse_reply`` and the
response cache see the same shape the API returns.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from ingest.voyage import Embedding
from retrieve.answer_model import ApiResponse
from tests.anthropic_fixtures import load, with_text

RECORDED = load("answered_q0072")["response"]
Reply = Mapping[str, Any] | Callable[[str], Mapping[str, Any]]  # the JSON, or prompt -> JSON


def schema_title(request) -> str:
    return request.params()["output_config"]["format"]["schema"].get("title", "")


class ScriptedBackend:
    """Replies by output schema title; records every request it receives."""

    def __init__(self, replies: Mapping[str, Reply], stop_reason: str = "end_turn",
                 text: str | None = None):
        self._replies = replies
        self._stop_reason = stop_reason
        self._text = text  # a raw reply text that overrides the scripted JSON
        self.requests: list = []

    def complete(self, request) -> ApiResponse:
        self.requests.append(request)
        reply = self._replies.get(schema_title(request), {})
        prompt = request.params()["messages"][0]["content"]
        payload = reply(prompt) if callable(reply) else reply
        text = self._text if self._text is not None else json.dumps(payload)
        body = {**with_text(RECORDED, text), "stop_reason": self._stop_reason}
        return ApiResponse(body=body, api_ms=100.0)


class FixedEmbedder:
    """Embeds by lookup; unknown text gets the fallback vector.  Counts its calls."""

    def __init__(self, vectors: Mapping[str, tuple[float, ...]],
                 fallback: tuple[float, ...] = (0.0, 1.0), tokens: int = 5):
        self._vectors = vectors
        self._fallback = fallback
        self._tokens = tokens
        self.texts: list[str] = []

    def embed_query(self, text: str, model: str) -> Embedding:
        self.texts.append(text)
        return Embedding(vector=self._vectors.get(text, self._fallback),
                         api_token_count=self._tokens)
