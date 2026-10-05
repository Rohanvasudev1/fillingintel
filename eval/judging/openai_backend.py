"""The OpenAI Responses API backend the judge calls, and its reply parser (Step 5, ticket 09).

``OpenAIResponsesModel`` implements the answer-model protocol
(``complete(request) -> ApiResponse``), so the response cache wraps it unchanged.
It makes one ``responses.create`` call per request and returns the raw body.

The API key is held by the SDK client only; it never reaches a log line, an
exception message or a result.  The client's address is set here, and
``from_env`` refuses to start when ``OPENAI_BASE_URL`` is set, so a stray shell
variable cannot send the key to another host (invariant 7).
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any

import openai
from openai.types.responses import Response
from pydantic import ValidationError

from retrieve.answer_model import AnswerModelError, ApiResponse, ModelRequest

API_KEY_ENV = "OPENAI_API_KEY"
BASE_URL_ENV = "OPENAI_BASE_URL"
API_BASE_URL = "https://api.openai.com/v1"
MAX_RETRIES = 6  # the SDK backs off on 408, 409, 429, 5xx and connection errors
_ERROR_MESSAGE_CHARS = 300
_MS_PER_SECOND = 1000.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenAIUsage:
    """Token counts as the Responses API reports them."""

    input_tokens: int  # includes the cached and cache-write tokens
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int  # includes the reasoning tokens
    reasoning_tokens: int


NO_USAGE = OpenAIUsage(0, 0, 0, 0, 0)


def total_usage(usages: Iterable[OpenAIUsage]) -> OpenAIUsage:
    """The field-by-field sum of *usages*."""
    listed = list(usages)
    return OpenAIUsage(
        input_tokens=sum(u.input_tokens for u in listed),
        cached_input_tokens=sum(u.cached_input_tokens for u in listed),
        cache_write_tokens=sum(u.cache_write_tokens for u in listed),
        output_tokens=sum(u.output_tokens for u in listed),
        reasoning_tokens=sum(u.reasoning_tokens for u in listed),
    )


@dataclass(frozen=True)
class OpenAIReply:
    """The parts of a Responses API body the judge uses."""

    response_id: str
    model: str
    status: str | None
    incomplete_reason: str | None
    refusal: str | None  # the refusal text, if the model refused
    text: str  # the output text items joined; reasoning items are left out
    usage: OpenAIUsage


def _usage(response: Response) -> OpenAIUsage:
    usage = response.usage
    if usage is None:
        raise AnswerModelError("unexpected response shape: no usage reported")
    return OpenAIUsage(
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.input_tokens_details.cached_tokens,
        cache_write_tokens=usage.input_tokens_details.cache_write_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.output_tokens_details.reasoning_tokens,
    )


def parse_openai_reply(body: Mapping[str, Any]) -> OpenAIReply:
    """Validate *body* as a Responses API response and pick out its parts."""
    try:
        response = Response.model_validate(body)
    except ValidationError as exc:
        raise AnswerModelError(f"unexpected response shape: {exc.error_count()} errors") from exc
    content = [c for item in response.output if item.type == "message" for c in item.content]
    refusals = [c.refusal for c in content if c.type == "refusal"]
    details = response.incomplete_details
    return OpenAIReply(
        response_id=response.id,
        model=response.model,
        status=response.status,
        incomplete_reason=details.reason if details is not None else None,
        refusal=refusals[0] if refusals else None,
        text="".join(c.text for c in content if c.type == "output_text"),
        usage=_usage(response),
    )


class OpenAIResponsesModel:
    """Calls ``responses.create`` once per request; the SDK retries transient failures."""

    def __init__(self, client: Any, clock: Callable[[], float] = time.perf_counter):
        self._client = client
        self._clock = clock

    @classmethod
    def from_env(cls) -> OpenAIResponsesModel:
        """A client keyed from ``OPENAI_API_KEY`` and pointed at OpenAI's API."""
        if os.environ.get(BASE_URL_ENV):
            raise AnswerModelError(
                f"{BASE_URL_ENV} is set; unset it, the judge only calls {API_BASE_URL}")
        key = os.environ.get(API_KEY_ENV, "")
        if not key:
            raise AnswerModelError(
                f"{API_KEY_ENV} is not set (run with: uv run --env-file .env ...)")
        return cls(openai.OpenAI(api_key=key, base_url=API_BASE_URL, max_retries=MAX_RETRIES))

    def __repr__(self) -> str:  # never show the key
        return "OpenAIResponsesModel()"

    def __enter__(self) -> OpenAIResponsesModel:
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

    def complete(self, request: ModelRequest) -> ApiResponse:
        start = self._clock()
        try:
            response = self._client.responses.create(**request.params())
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            # The API's message quotes part of the key, so it is not repeated here.
            raise AnswerModelError(
                f"OpenAI API call failed: {type(exc).__name__} (HTTP {exc.status_code}); "
                f"check {API_KEY_ENV} and the project's model access"
            ) from None
        except openai.APIStatusError as exc:
            raise AnswerModelError(
                f"OpenAI API call failed: {type(exc).__name__} (HTTP {exc.status_code}): "
                f"{exc.message[:_ERROR_MESSAGE_CHARS]}"
            ) from exc
        except openai.APIError as exc:
            raise AnswerModelError(f"OpenAI API call failed: {type(exc).__name__}") from exc
        elapsed_ms = (self._clock() - start) * _MS_PER_SECOND
        if response.model != request.model:
            logger.warning("asked for %s, response is from %s", request.model, response.model)
        # to_dict keeps the API's field names; model_dump would write ``schema`` as ``schema_``.
        return ApiResponse(body=response.to_dict(mode="json"), api_ms=elapsed_ms)
