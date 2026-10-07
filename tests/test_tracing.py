"""The tracing module on its own: the off switch, the provider and span attributes (Step 6).

Spans are checked through an in-memory exporter on a provider each test builds;
nothing here sends a trace.  The traced eval run is in ``test_tracing_run.py``.
"""
import os
import socket
import time

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from retrieve.answer import write_answer
from retrieve.answer_model import ApiResponse
from retrieve.answer_prompt import SourceChunk, load_prompt
from retrieve.arm import RetrievedChunk
from retrieve.tracing import (
    COST_ATTR,
    ENDPOINT_ENV,
    TEXT_ENV,
    Kind,
    SpanRecorder,
    TracingConfigError,
    build_provider,
    generation_attributes,
    retrieval_attributes,
    text_capture,
    trace_url,
)
from tests.anthropic_fixtures import load, with_text

CHUNK_ID = "0001045810-26-000021:0012"
TOKEN_PREFIX = "llm.token_count."


@pytest.fixture
def exporter():
    spans = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(spans))
    yield provider, spans
    provider.shutdown()


def _answer(usage: dict | None = None, cached: bool = False):
    body = with_text(load("answered_q0072")["response"], f"STATUS: answered\nIt rose [{CHUNK_ID}].")
    if usage is not None:
        body = {**body, "usage": {**body["usage"], **usage}}

    class Model:
        def complete(self, request):
            return ApiResponse(body, api_ms=900.0, from_cache=cached)

    chunk = SourceChunk(CHUNK_ID, "NVDA", "10-K", "FY2026", "part_ii_item_7", "Revenue rose.")
    return write_answer("How did revenue change?", [chunk], Model(), load_prompt())


# ── off switch and provider ───────────────────────────────────────────────────

def test_tests_never_see_the_collector_endpoint_or_otlp_settings():
    assert ENDPOINT_ENV not in os.environ
    assert not [name for name in os.environ if name.startswith("OTEL_EXPORTER_OTLP_")]


def test_no_provider_is_built_when_the_endpoint_is_unset():
    assert build_provider(os.environ) is None
    assert build_provider({ENDPOINT_ENV: "  "}) is None


def test_a_provider_is_built_when_the_endpoint_is_set():
    provider = build_provider({ENDPOINT_ENV: "http://localhost:6006"})
    try:
        assert isinstance(provider, TracerProvider)
        assert provider.resource.attributes["openinference.project.name"] == "filingintel"
    finally:
        provider.shutdown()


@pytest.mark.parametrize(("endpoint", "url"), [
    ("http://localhost:6006", "http://localhost:6006/v1/traces"),
    ("http://localhost:6006/", "http://localhost:6006/v1/traces"),
    ("https://127.0.0.1:6006", "https://127.0.0.1:6006/v1/traces"),
])
def test_spans_go_to_the_collectors_traces_path(endpoint, url):
    assert trace_url(endpoint) == url


@pytest.mark.parametrize("endpoint", ["localhost:6006", "ftp://localhost:6006", "http://",
                                      "http://user:pw@localhost:6006",
                                      "http://localhost:6006?api_key=x", "http://localhost:6006#x"])
def test_a_malformed_endpoint_is_refused(endpoint):
    with pytest.raises(TracingConfigError):
        build_provider({ENDPOINT_ENV: endpoint})


def test_an_unreachable_collector_costs_at_most_a_few_seconds_at_shutdown():
    with socket.socket() as probe:  # a port nothing listens on once the probe closes
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    provider = build_provider({ENDPOINT_ENV: f"http://127.0.0.1:{port}"})
    tracer = provider.get_tracer("test")
    for name in ("a", "b", "c"):
        with tracer.start_as_current_span(name):
            pass
    start = time.perf_counter()
    provider.shutdown()
    assert time.perf_counter() - start < 4.0


@pytest.mark.parametrize(("value", "expected"), [
    (None, False), ("", False), ("false", False), ("FALSE", False), ("true", True), ("True", True),
])
def test_text_capture_is_off_unless_set_to_true(value, expected):
    environ = {} if value is None else {TEXT_ENV: value}
    assert text_capture(environ) is expected


def test_an_unrecognised_text_capture_value_is_refused():
    with pytest.raises(TracingConfigError, match=TEXT_ENV):
        text_capture({TEXT_ENV: "yes"})


# ── spans and attributes ──────────────────────────────────────────────────────

def test_the_off_recorder_records_nothing(exporter):
    provider, spans = exporter
    with provider.get_tracer("test").start_as_current_span("root"):
        with SpanRecorder.off().span("child", Kind.CHAIN) as span:
            assert not span.is_recording()
    assert [s.name for s in spans.get_finished_spans()] == ["root"]


def test_a_span_carries_its_openinference_kind_and_attributes(exporter):
    provider, spans = exporter
    recorder = SpanRecorder(provider.get_tracer("test"))
    with recorder.span("vector_search", Kind.RETRIEVER, {"input.value": "q"}):
        pass
    (span,) = spans.get_finished_spans()
    assert dict(span.attributes) == {"openinference.span.kind": "RETRIEVER", "input.value": "q"}


def test_retrieved_chunks_are_listed_with_their_scores_in_rank_order():
    ranked = (RetrievedChunk("a:0001", 0.9), RetrievedChunk("b:0002", 0.8))
    attributes = retrieval_attributes(ranked)
    assert attributes == {
        "retrieval.documents.0.document.id": "a:0001",
        "retrieval.documents.0.document.score": 0.9,
        "retrieval.documents.1.document.id": "b:0002",
        "retrieval.documents.1.document.score": 0.8,
    }


def test_claudes_prompt_count_includes_cache_reads_and_writes():
    answer = _answer({"input_tokens": 1000, "output_tokens": 300,
                      "cache_read_input_tokens": 200, "cache_creation_input_tokens": 50})
    attributes = generation_attributes(answer, messages=None)
    assert attributes["llm.token_count.prompt"] == 1250
    assert attributes["llm.token_count.prompt_details.cache_read"] == 200
    assert attributes["llm.token_count.prompt_details.cache_write"] == 50
    assert attributes["llm.token_count.completion"] == 300
    assert attributes["llm.token_count.total"] == 1550
    assert attributes["filingintel.cache_hit"] is False
    assert attributes[COST_ATTR] == pytest.approx(answer.cost_usd)


def test_a_cache_replay_carries_no_token_counts_but_keeps_its_cost():
    answer = _answer(cached=True)
    attributes = generation_attributes(answer, messages=None)
    assert not [k for k in attributes if k.startswith(TOKEN_PREFIX)]
    assert attributes["filingintel.cache_hit"] is True
    assert attributes[COST_ATTR] == pytest.approx(answer.cost_usd)


def test_answer_text_is_left_out_unless_messages_are_given():
    answer = _answer()
    off = generation_attributes(answer, messages=None)
    assert not [k for k in off if k.startswith(("llm.input_messages", "llm.output_messages",
                                                "output.value"))]
    on = generation_attributes(answer, messages=("the system", "the user"))
    assert on["llm.input_messages.0.message.role"] == "system"
    assert on["llm.input_messages.0.message.content"] == "the system"
    assert on["llm.input_messages.1.message.role"] == "user"
    assert on["llm.input_messages.1.message.content"] == "the user"
    assert on["llm.output_messages.0.message.role"] == "assistant"
    assert on["llm.output_messages.0.message.content"] == answer.raw_text
    assert on["output.value"] == answer.text
