"""Recorded Anthropic responses in ``tests/fixtures/anthropic/`` and a fake SDK client
that replays them, so no test touches the network."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from anthropic.types import Message

FIXTURES = Path(__file__).parent / "fixtures" / "anthropic"
RECORDED = ("answered_q0072", "declined_q0005", "not_found_q0011")


def load(name: str) -> dict:
    """One recorded fixture: the question, its retrieved chunk IDs and the response body."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def with_text(body: dict, text: str) -> dict:
    """*body* with its text blocks replaced by one holding *text*; other blocks are kept."""
    content = [b for b in body["content"] if b["type"] != "text"]
    return {**body, "content": [*content, {"type": "text", "text": text}]}


class FakeAnthropicClient:
    """Stands in for ``anthropic.Anthropic``: records each call's arguments and
    returns the recorded response, or raises *error*."""

    def __init__(self, body: dict | None = None, error: Exception | None = None):
        self.calls: list[dict] = []
        self.closed = False
        self._body = body
        self._error = error
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs) -> Message:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return Message.model_validate(self._body)

    def close(self) -> None:
        self.closed = True
