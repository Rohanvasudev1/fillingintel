"""Fakes for the judge tests: a scripted Responses API backend and a fixed embedder.

The backend answers each request with a reply chosen by the requested output
schema's name, wrapped in a real recorded ``gpt-6-luna`` response body, so the
reply parser and the response cache see the same shape the API returns.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from ingest.voyage import Embedding
from retrieve.answer_model import ApiResponse
from tests.openai_fixtures import RECORDED, with_text

Reply = Mapping[str, Any] | Callable[[str], Mapping[str, Any]]  # the JSON, or prompt -> JSON


def schema_title(request) -> str:
    return request.params()["text"]["format"]["name"]


def prompt_of(request) -> str:
    return request.params()["input"][0]["content"]


class ScriptedBackend:
    """Replies by output schema name; records every request it receives."""

    def __init__(self, replies: Mapping[str, Reply], text: str | None = None,
                 body: Mapping[str, Any] | None = None):
        self._replies = replies
        self._text = text  # a raw reply text that overrides the scripted JSON
        self._body = body  # a whole response body that overrides both
        self.requests: list = []

    def complete(self, request) -> ApiResponse:
        self.requests.append(request)
        if self._body is not None:
            return ApiResponse(body=self._body, api_ms=100.0)
        reply = self._replies.get(schema_title(request), {})
        payload = reply(prompt_of(request)) if callable(reply) else reply
        text = self._text if self._text is not None else json.dumps(payload)
        return ApiResponse(body=with_text(RECORDED, text), api_ms=100.0)


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
