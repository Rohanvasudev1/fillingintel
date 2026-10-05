"""The answer model: one Messages API call per question (Step 5).

``claude-sonnet-5-5`` writes answers at effort ``high``.  The model rejects
non-default sampling settings and thinking is always on, so a request carries
only the model, ``max_tokens`` (which covers thinking plus text), the system
prompt, one user message and the effort.  Reruns reproduce answers through the
response cache (``retrieve.response_cache``), not through sampling settings.

No refusal fallback is configured: a fallback would let another model write the
answer, so a refusal is recorded as one instead.  The API key is held by the SDK
client only; it never reaches a log line, an exception message or a result.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal, Protocol

import anthropic
from anthropic.types import Message
from pydantic import ValidationError

ANSWER_MODEL = "claude-sonnet-5-5"
ANSWER_EFFORT = "high"
ANSWER_MAX_TOKENS = 16_000  # thinking plus text; non-streaming calls stay under SDK timeouts
API_KEY_ENV = "ANTHROPIC_API_KEY"
MAX_RETRIES = 6  # the SDK backs off on 408, 409, 429, 5xx and connection errors
_ERROR_MESSAGE_CHARS = 300
_MS_PER_SECOND = 1000.0

Effort = Literal["low", "medium", "high", "xhigh", "max"]
logger = logging.getLogger(__name__)


class AnswerModelError(RuntimeError):
    """The API call failed or returned something this code does not accept."""


class MissingApiKey(AnswerModelError):
    """``ANTHROPIC_API_KEY`` is not set."""


@dataclass(frozen=True)
class AnswerRequest:
    """Everything sent to the API; its hash is the response cache key."""

    model: str
    effort: Effort
    max_tokens: int
    system: str
    user: str

    def params(self) -> dict[str, object]:
        """The keyword arguments for ``messages.create``."""
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system,
            "messages": [{"role": "user", "content": self.user}],
            "output_config": {"effort": self.effort},
        }

    def cache_key(self) -> str:
        """SHA-256 of the full request, so any change to the prompt is a new key."""
        canonical = json.dumps(self.params(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApiResponse:
    body: Mapping[str, Any]  # the Messages API response, as JSON
    api_ms: float  # how long the API call took when it was made
    from_cache: bool = False


class AnswerModel(Protocol):
    def complete(self, request: AnswerRequest) -> ApiResponse: ...


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int  # includes thinking tokens
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(frozen=True)
class ModelReply:
    """The parts of a response the arm uses."""

    message_id: str
    model: str
    text: str  # the text blocks joined; thinking blocks are left out
    stop_reason: str | None
    refusal_category: str | None
    usage: TokenUsage


def parse_reply(body: Mapping[str, Any]) -> ModelReply:
    """Validate *body* as a Messages API response and pick out its parts."""
    try:
        message = Message.model_validate(body)
    except ValidationError as exc:
        raise AnswerModelError(f"unexpected response shape: {exc.error_count()} errors") from exc
    usage = message.usage
    details = message.stop_details
    return ModelReply(
        message_id=message.id,
        model=message.model,
        text="".join(block.text for block in message.content if block.type == "text"),
        stop_reason=message.stop_reason,
        refusal_category=details.category if details is not None else None,
        usage=TokenUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens or 0,
            cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
        ),
    )


class AnthropicAnswerModel:
    """Calls ``messages.create`` once per request; the SDK retries transient failures."""

    def __init__(self, client: Any, clock: Callable[[], float] = time.perf_counter):
        self._client = client
        self._clock = clock

    @classmethod
    def from_env(cls) -> AnthropicAnswerModel:
        """A client keyed from ``ANTHROPIC_API_KEY``; raises ``MissingApiKey`` if unset."""
        key = os.environ.get(API_KEY_ENV, "")
        if not key:
            raise MissingApiKey(f"{API_KEY_ENV} is not set (run with: uv run --env-file .env ...)")
        return cls(anthropic.Anthropic(api_key=key, max_retries=MAX_RETRIES))

    def __repr__(self) -> str:  # never show the key
        return "AnthropicAnswerModel()"

    def __enter__(self) -> AnthropicAnswerModel:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the SDK's connection pool."""
        self._client.close()

    def complete(self, request: AnswerRequest) -> ApiResponse:
        start = self._clock()
        try:
            message = self._client.messages.create(**request.params())
        except anthropic.APIStatusError as exc:
            raise AnswerModelError(
                f"Anthropic API call failed: {type(exc).__name__} (HTTP {exc.status_code}): "
                f"{exc.message[:_ERROR_MESSAGE_CHARS]}"
            ) from exc
        except anthropic.APIError as exc:
            raise AnswerModelError(f"Anthropic API call failed: {type(exc).__name__}") from exc
        elapsed_ms = (self._clock() - start) * _MS_PER_SECOND
        if message.model != request.model:
            logger.warning("asked for %s, response is from %s", request.model, message.model)
        return ApiResponse(body=message.model_dump(mode="json"), api_ms=elapsed_ms)
