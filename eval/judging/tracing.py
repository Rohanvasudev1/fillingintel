"""Span attributes for the judges (Step 6, ticket 08).

Each judged metric of one judge run is an EVALUATOR span named after the
metric; each judge call under it is an LLM span.  The rules match the answer
spans in ``retrieve.tracing``: a response-cache replay carries no
``llm.token_count.*``, and ``filingintel.cost_usd`` holds the cost the run file
records.  Prompt text is not put on judge spans.

OpenAI's ``input_tokens`` already includes cached and cache-write tokens, and
``output_tokens`` includes reasoning, so they map to the prompt and completion
counts as they are (docs/research/phoenix-tracing.md, section 3).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from openinference.semconv.trace import (
    OpenInferenceLLMProviderValues,
    OpenInferenceLLMSystemValues,
    OpenInferenceMimeTypeValues,
    SpanAttributes,
)
from opentelemetry.util.types import AttributeValue

from retrieve.answer_model import Effort
from retrieve.tracing import CACHE_HIT_ATTR, COST_ATTR

if TYPE_CHECKING:
    from eval.judging.openai_judge import JudgeCall

JUDGE_CALL_SPAN = "judge_call"
METRIC_ATTR = "filingintel.judge.metric"
RUN_ATTR = "filingintel.judge.run"
ERROR_ATTR = "filingintel.judge.error"  # why the judge's reply could not be scored

_JSON = OpenInferenceMimeTypeValues.JSON.value


def evaluator_attributes(metric: str, run: int) -> dict[str, AttributeValue]:
    return {METRIC_ATTR: metric, RUN_ATTR: run}


def score_attributes(score: float | None, error: str | None) -> dict[str, AttributeValue]:
    """The metric's outcome: its score as JSON (``null`` when skipped or failed)."""
    attributes: dict[str, AttributeValue] = {
        SpanAttributes.OUTPUT_VALUE: json.dumps(score),
        SpanAttributes.OUTPUT_MIME_TYPE: _JSON,
    }
    if error is not None:
        attributes[ERROR_ATTR] = error
    return attributes


def judge_request_attributes(model: str, effort: Effort,
                             max_output_tokens: int) -> dict[str, AttributeValue]:
    """The judge request, known before the call: model, provider and settings."""
    return {
        SpanAttributes.LLM_MODEL_NAME: model,
        SpanAttributes.LLM_PROVIDER: OpenInferenceLLMProviderValues.OPENAI.value,
        SpanAttributes.LLM_SYSTEM: OpenInferenceLLMSystemValues.OPENAI.value,
        SpanAttributes.LLM_INVOCATION_PARAMETERS: json.dumps(
            {"reasoning": {"effort": effort}, "max_output_tokens": max_output_tokens}
        ),
    }


def judge_call_attributes(call: JudgeCall) -> dict[str, AttributeValue]:
    """The call's outcome; token counts only when the API was actually called."""
    attributes: dict[str, AttributeValue] = {
        CACHE_HIT_ATTR: call.from_cache,
        COST_ATTR: call.cost_usd,
    }
    if not call.from_cache:
        usage = call.usage
        attributes.update({
            SpanAttributes.LLM_TOKEN_COUNT_PROMPT: usage.input_tokens,
            SpanAttributes.LLM_TOKEN_COUNT_PROMPT_DETAILS_CACHE_READ: usage.cached_input_tokens,
            SpanAttributes.LLM_TOKEN_COUNT_PROMPT_DETAILS_CACHE_WRITE: usage.cache_write_tokens,
            SpanAttributes.LLM_TOKEN_COUNT_COMPLETION: usage.output_tokens,
            SpanAttributes.LLM_TOKEN_COUNT_COMPLETION_DETAILS_REASONING: usage.reasoning_tokens,
            SpanAttributes.LLM_TOKEN_COUNT_TOTAL: usage.input_tokens + usage.output_tokens,
        })
    return attributes
