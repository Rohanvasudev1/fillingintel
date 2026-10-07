"""OpenTelemetry tracing to Phoenix, with OpenInference span names (Step 6).

Tracing is off unless ``PHOENIX_COLLECTOR_ENDPOINT`` is set.  Off means no SDK
object is built: callers get ``SpanRecorder.off()``, whose spans record nothing.
On, ``build_provider`` returns a provider that batches spans to
``{endpoint}/v1/traces`` over OTLP HTTP with a 2-second export timeout, so a
stopped Phoenix costs about a second at shutdown.  The caller owns the provider
and shuts it down; this module never sets the global tracer provider.

Question text is always recorded.  Prompt and answer text go on spans only when
``FILINGINTEL_TRACE_TEXT`` is ``true``.  No API key or request header is ever an
attribute.  A response-cache replay carries no ``llm.token_count.*``, so Phoenix
does not price a call that was never made; our own cost figure, the one the run
file records, goes in ``filingintel.cost_usd`` on every span that has a cost.
(docs/research/phoenix-tracing.md, sections 2 to 4.)
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from openinference.semconv.resource import ResourceAttributes
from openinference.semconv.trace import (
    DocumentAttributes,
    EmbeddingAttributes,
    MessageAttributes,
    OpenInferenceLLMProviderValues,
    OpenInferenceLLMSystemValues,
    OpenInferenceMimeTypeValues,
    OpenInferenceSpanKindValues,
    SpanAttributes,
)
from opentelemetry.trace import NoOpTracer, Span, Tracer
from opentelemetry.util.types import AttributeValue

from ingest.voyage import QUERY_INPUT, Embedding
from retrieve.answer import Answer
from retrieve.arm import RetrievedChunk
from retrieve.question_filter import QuestionFilter

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

ENDPOINT_ENV = "PHOENIX_COLLECTOR_ENDPOINT"
TEXT_ENV = "FILINGINTEL_TRACE_TEXT"
PROJECT = "filingintel"
TRACES_PATH = "/v1/traces"
EXPORT_TIMEOUT_S = 2

QUESTION_SPAN = "answer_question"
FILTER_SPAN = "question_filter"
EMBED_SPAN = "query_embedding"
SEARCH_SPAN = "vector_search"
GENERATION_SPAN = "answer_generation"

COST_ATTR = "filingintel.cost_usd"  # Phoenix ignores llm.cost.*, and has no Voyage price
CACHE_HIT_ATTR = "filingintel.cache_hit"
EMBED_TOKENS_ATTR = "filingintel.embedding.token_count"  # as Voyage reported it
ANSWER_STATUS_ATTR = "filingintel.answer.status"

Kind = OpenInferenceSpanKindValues
Attributes = Mapping[str, AttributeValue]
_JSON = OpenInferenceMimeTypeValues.JSON.value
_TEXT = OpenInferenceMimeTypeValues.TEXT.value
_TRUE, _FALSE = "true", "false"

logger = logging.getLogger(__name__)


class TracingConfigError(ValueError):
    """A tracing setting has a value this module does not accept; the message is safe to print."""


def trace_url(endpoint: str) -> str:
    """The OTLP HTTP traces URL for a collector base URL such as ``http://localhost:6006``.

    The exporter uses its ``endpoint`` argument as given, so the path is added here.
    """
    parts = urlsplit(endpoint.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise TracingConfigError(f"{ENDPOINT_ENV} must be an http(s) URL such as "
                                 "http://localhost:6006")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise TracingConfigError(f"{ENDPOINT_ENV} must be a base URL with no credentials, "
                                 "query or fragment")
    return endpoint.strip().rstrip("/") + TRACES_PATH


def build_provider(environ: Mapping[str, str]) -> TracerProvider | None:
    """A provider exporting to Phoenix, or None when ``PHOENIX_COLLECTOR_ENDPOINT`` is unset.

    The SDK and exporter are imported only when tracing is on.  The caller shuts
    the provider down, which flushes the spans still queued.
    """
    endpoint = environ.get(ENDPOINT_ENV, "").strip()
    if not endpoint:
        return None
    url = trace_url(endpoint)
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON

    provider = TracerProvider(resource=Resource.create({ResourceAttributes.PROJECT_NAME: PROJECT}),
                              sampler=ALWAYS_ON)  # a stray OTEL_TRACES_SAMPLER cannot drop spans
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=url, timeout=EXPORT_TIMEOUT_S))
    )
    logger.info("tracing to %s", url)
    return provider


def text_capture(environ: Mapping[str, str]) -> bool:
    """Whether prompt and answer text go on spans: ``FILINGINTEL_TRACE_TEXT`` is ``true``."""
    value = environ.get(TEXT_ENV, "").strip().lower()
    if value not in ("", _TRUE, _FALSE):
        raise TracingConfigError(f"{TEXT_ENV} must be true or false")
    return value == _TRUE


class SpanRecorder:
    """Starts OpenInference spans on one tracer and holds the text-capture setting."""

    def __init__(self, tracer: Tracer, capture_text: bool = False):
        self._tracer = tracer
        self._capture_text = capture_text

    @classmethod
    def off(cls) -> SpanRecorder:
        """A recorder whose spans record nothing and are never exported."""
        return cls(NoOpTracer())

    @property
    def capture_text(self) -> bool:
        return self._capture_text

    def span(self, name: str, kind: Kind,
             attributes: Attributes | None = None) -> AbstractContextManager[Span]:
        """A span of *kind*, current for the ``with`` block, so spans inside it are children.

        An exception leaving the block is recorded on the span and re-raised.
        """
        return self._tracer.start_as_current_span(
            name, attributes={SpanAttributes.OPENINFERENCE_SPAN_KIND: kind.value,
                              **(attributes or {})},
        )


def question_input(question: str) -> dict[str, AttributeValue]:
    """A span's input: the question text, which is always recorded."""
    return {SpanAttributes.INPUT_VALUE: question, SpanAttributes.INPUT_MIME_TYPE: _TEXT}


def question_attributes(question: str, metadata: Mapping[str, str]) -> dict[str, AttributeValue]:
    """The root span: the question text, and the record's ID, set, arm, split and class."""
    return {
        **question_input(question),
        SpanAttributes.METADATA: json.dumps(dict(metadata), sort_keys=True),
    }


def status_attributes(status: str) -> dict[str, AttributeValue]:
    """The root span's output: the answer status, never the answer text."""
    return {
        SpanAttributes.OUTPUT_VALUE: status,
        SpanAttributes.OUTPUT_MIME_TYPE: _TEXT,
        ANSWER_STATUS_ATTR: status,
    }


def filter_attributes(question_filter: QuestionFilter) -> dict[str, AttributeValue]:
    return {
        SpanAttributes.OUTPUT_VALUE: json.dumps(question_filter.as_dict()),
        SpanAttributes.OUTPUT_MIME_TYPE: _JSON,
    }


def embedding_attributes(model: str, question: str) -> dict[str, AttributeValue]:
    """The query embedding request.  The vector itself is never recorded."""
    prefix = f"{SpanAttributes.EMBEDDING_EMBEDDINGS}.0."
    return {
        SpanAttributes.EMBEDDING_MODEL_NAME: model,
        SpanAttributes.EMBEDDING_INVOCATION_PARAMETERS: json.dumps({"input_type": QUERY_INPUT}),
        prefix + EmbeddingAttributes.EMBEDDING_TEXT: question,
    }


def embedding_result_attributes(embedding: Embedding, cost_usd: float) -> dict[str, AttributeValue]:
    return {
        CACHE_HIT_ATTR: embedding.from_cache,
        EMBED_TOKENS_ATTR: embedding.api_token_count,
        COST_ATTR: cost_usd,
    }


def retrieval_attributes(retrieved: Sequence[RetrievedChunk]) -> dict[str, AttributeValue]:
    """Each retrieved chunk's ID and score, best first."""
    attributes: dict[str, AttributeValue] = {}
    for i, chunk in enumerate(retrieved):
        prefix = f"{SpanAttributes.RETRIEVAL_DOCUMENTS}.{i}."
        attributes[prefix + DocumentAttributes.DOCUMENT_ID] = chunk.chunk_id
        attributes[prefix + DocumentAttributes.DOCUMENT_SCORE] = chunk.score
    return attributes


def generation_request_attributes(model: str, effort: str, max_tokens: int,
                                  prompt_version: str) -> dict[str, AttributeValue]:
    """The answer request, known before the call: model, provider and settings."""
    return {
        SpanAttributes.LLM_MODEL_NAME: model,
        SpanAttributes.LLM_PROVIDER: OpenInferenceLLMProviderValues.ANTHROPIC.value,
        SpanAttributes.LLM_SYSTEM: OpenInferenceLLMSystemValues.ANTHROPIC.value,
        SpanAttributes.LLM_INVOCATION_PARAMETERS: json.dumps(
            {"effort": effort, "max_tokens": max_tokens}
        ),
        SpanAttributes.LLM_PROMPT_TEMPLATE_VERSION: prompt_version,
    }


def generation_attributes(answer: Answer,
                          messages: tuple[str, str] | None) -> dict[str, AttributeValue]:
    """The answer call's outcome.  *messages* is (system, user) when text capture is on.

    Claude's ``input_tokens`` leaves out cache reads and writes, and Phoenix
    subtracts the cache details from the prompt count, so the prompt count is
    the sum of all three.  A cache replay gets no token counts at all.
    """
    attributes: dict[str, AttributeValue] = {
        SpanAttributes.LLM_MODEL_NAME: answer.model,
        CACHE_HIT_ATTR: answer.from_cache,
        COST_ATTR: answer.cost_usd,
        ANSWER_STATUS_ATTR: answer.status,
    }
    if answer.stop_reason is not None:
        attributes[SpanAttributes.LLM_FINISH_REASON] = answer.stop_reason
    if not answer.from_cache:
        attributes.update(_token_counts(answer))
    if messages is not None:
        attributes.update(_message_attributes(answer, messages))
    return attributes


def _token_counts(answer: Answer) -> dict[str, AttributeValue]:
    usage = answer.usage
    prompt = (usage.input_tokens + usage.cache_read_input_tokens
              + usage.cache_creation_input_tokens)
    return {
        SpanAttributes.LLM_TOKEN_COUNT_PROMPT: prompt,
        SpanAttributes.LLM_TOKEN_COUNT_PROMPT_DETAILS_CACHE_READ: usage.cache_read_input_tokens,
        SpanAttributes.LLM_TOKEN_COUNT_PROMPT_DETAILS_CACHE_WRITE:
            usage.cache_creation_input_tokens,
        SpanAttributes.LLM_TOKEN_COUNT_COMPLETION: usage.output_tokens,
        SpanAttributes.LLM_TOKEN_COUNT_TOTAL: prompt + usage.output_tokens,
    }


def _message_attributes(answer: Answer, messages: tuple[str, str]) -> dict[str, AttributeValue]:
    system, user = messages
    attributes: dict[str, AttributeValue] = {
        SpanAttributes.OUTPUT_VALUE: answer.text,
        SpanAttributes.OUTPUT_MIME_TYPE: _TEXT,
    }
    inputs = (("system", system), ("user", user))
    outputs = (("assistant", answer.raw_text),)
    for key, items in ((SpanAttributes.LLM_INPUT_MESSAGES, inputs),
                       (SpanAttributes.LLM_OUTPUT_MESSAGES, outputs)):
        for i, (role, content) in enumerate(items):
            attributes[f"{key}.{i}.{MessageAttributes.MESSAGE_ROLE}"] = role
            attributes[f"{key}.{i}.{MessageAttributes.MESSAGE_CONTENT}"] = content
    return attributes
