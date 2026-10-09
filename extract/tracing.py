"""Phoenix spans for an extraction run (Step 8), with the names and rules of Step 6.

The run is one CHAIN span; each chunk call is an LLM span under it, carrying
the model, settings, prompt version, chunk ID, cache hit, cost and, for a call
actually made, the token counts the API reported. A cache replay carries no
token counts, so Phoenix does not price a call that was never made. Chunk and
reply text go on spans only when `FILINGINTEL_TRACE_TEXT` is `true`.
"""
from __future__ import annotations

from openinference.semconv.trace import SpanAttributes
from opentelemetry.util.types import AttributeValue

from extract.request import ExtractRequest
from retrieve.answer_model import AnswerModel, AnswerModelError, ApiResponse, parse_reply
from retrieve.pricing import PriceTable, anthropic_cost
from retrieve.tracing import (
    CACHE_HIT_ATTR,
    COST_ATTR,
    Kind,
    SpanRecorder,
    generation_request_attributes,
    message_attributes,
    token_count_attributes,
)

RUN_SPAN = "extract_filing"
CHUNK_SPAN = "extract_chunk"
CHUNK_ID_ATTR = "filingintel.chunk_id"
ACCESSION_ATTR = "filingintel.accession_no"


def run_attributes(accession_no: str) -> dict[str, AttributeValue]:
    return {SpanAttributes.INPUT_VALUE: accession_no, ACCESSION_ATTR: accession_no}


class TracedExtractModel:
    """Wraps the (cached) model; each call is one LLM span on *spans*."""

    def __init__(self, inner: AnswerModel, spans: SpanRecorder, prompt_version: str,
                 prices: PriceTable):
        self._inner = inner
        self._spans = spans
        self._prompt_version = prompt_version
        self._prices = prices

    def complete(self, request: ExtractRequest) -> ApiResponse:
        attributes = {
            **generation_request_attributes(request.model, request.effort, request.max_tokens,
                                            self._prompt_version),
            CHUNK_ID_ATTR: request.chunk_id,
        }
        with self._spans.span(CHUNK_SPAN, Kind.LLM, attributes) as span:
            response = self._inner.complete(request)
            span.set_attributes(self._outcome(request, response))
            return response

    def _outcome(self, request: ExtractRequest,
                 response: ApiResponse) -> dict[str, AttributeValue]:
        attributes: dict[str, AttributeValue] = {CACHE_HIT_ATTR: response.from_cache}
        try:
            reply = parse_reply(response.body)
        except AnswerModelError:  # extract_filing() records the failure; the span has no usage
            return attributes
        attributes[COST_ATTR] = anthropic_cost(request.model, reply.usage, self._prices)
        if reply.stop_reason is not None:
            attributes[SpanAttributes.LLM_FINISH_REASON] = reply.stop_reason
        if not response.from_cache:
            attributes.update(token_count_attributes(reply.usage))
        if self._spans.capture_text:
            attributes.update(message_attributes(request.system, request.user, reply.text))
        return attributes
