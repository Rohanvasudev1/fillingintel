"""The router's classifier: one claude-haiku-5-5 call per question (Step 13).

The request carries structured output ``{reason, predicted_class}`` (a short
reason, then an enum of the five predicted classes), the effort asked for, no
sampling settings and no tools; the system prompt is one block with the only
cache breakpoint.  The question text is the only input.  A reply that is cut
off, refused or off the schema raises ``RouteError``: there is no default
class.  ``ROUTE_EFFORT`` and ``ROUTE_PROMPT_VERSION`` are the settings frozen
from the dev sweep (``eval.route``).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from string import Template
from typing import Any, Literal, cast

from ingest.chunker import count_tokens
from retrieve.answer_model import AnswerModel, Effort, TokenUsage, parse_reply
from retrieve.arm import StageCost
from retrieve.pricing import TABLE_2026_10_10
from retrieve.route_prompt import PREDICTED_CLASSES, RoutePrompt, route_output_schema
from retrieve.tracing import (
    ROUTE_CLASSIFIER_SPAN,
    Kind,
    SpanRecorder,
    classifier_attributes,
    generation_request_attributes,
)

ROUTE_MODEL = "claude-haiku-5-5"
# Chosen on dev (benchmarks/router/route-run1..4.json): route/v1 scored 75, 72 and 71 of 88
# at low, medium and high, so low; route/v2 at low scored 76 of 88, so v2.
ROUTE_EFFORT: Effort = "low"
ROUTE_PROMPT_VERSION = "v2"
ROUTE_MAX_TOKENS = 16_000  # thinking plus the reply, as for the other Haiku calls
ROUTE_PRICES = TABLE_2026_10_10  # Haiku 5.5 at its up-to-100K-token prices
EFFORT_CHOICES: tuple[Effort, ...] = ("low", "medium", "high")  # the dev sweep, in order

# The estimate: cl100k_base counts times TOKEN_MARGIN (Haiku's tokenizer differs), a question
# of ASSUMED_QUESTION_TOKENS when its text is not read, and ASSUMED_OUTPUT_TOKENS of thinking
# and reply per call.
TOKEN_MARGIN = 2.0
ASSUMED_QUESTION_TOKENS = 100
ASSUMED_OUTPUT_TOKENS = 3_000

PredictedClass = Literal["lookup", "local", "multi_hop", "global", "decline"]
DECLINE: PredictedClass = "decline"
_STOPPED = {"max_tokens": "cut off at max_tokens", "refusal": "refused"}


class RouteError(RuntimeError):
    """The classifier's reply cannot be used; the message is safe to print."""


@dataclass(frozen=True)
class RouteRequest:
    """Everything sent for one question's classification."""

    system: str
    user: str
    effort: Effort
    model: str = ROUTE_MODEL
    max_tokens: int = ROUTE_MAX_TOKENS

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
                "format": {"type": "json_schema", "schema": route_output_schema()},
            },
        }

    def cache_key(self) -> str:
        """SHA-256 of the full request."""
        canonical = json.dumps(self.params(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RouteReply:
    predicted_class: PredictedClass
    reason: str
    usage: TokenUsage
    text: str  # the reply as sent, for a span with text capture on


@dataclass(frozen=True)
class Classification:
    """One question's predicted class, why, and what the call cost."""

    predicted_class: PredictedClass
    reason: str
    effort: Effort
    prompt_version: str  # e.g. "route/v1@1a2b3c4d"
    prompt_sha256: str
    cost: StageCost  # ms is the call's API latency when it was made


def estimate_request_usd(request: RouteRequest) -> float:
    """What one classifier call is assumed to cost, uncached."""
    price = ROUTE_PRICES.price(ROUTE_MODEL)
    tokens = (count_tokens(request.system) + count_tokens(request.user)) * TOKEN_MARGIN
    return (tokens * price.input_per_mtok
            + ASSUMED_OUTPUT_TOKENS * price.output_per_mtok) / 1_000_000


def estimate_classification_usd(prompt: RoutePrompt, questions: int) -> float:
    """*questions* classifier calls, uncached, each question assumed
    ``ASSUMED_QUESTION_TOKENS`` long (an estimate never reads question text)."""
    request = build_route_request(prompt, "", ROUTE_EFFORT)
    price = ROUTE_PRICES.price(ROUTE_MODEL)
    per_call = estimate_request_usd(request) + (
        ASSUMED_QUESTION_TOKENS * TOKEN_MARGIN * price.input_per_mtok / 1_000_000)
    return per_call * questions


def build_route_request(prompt: RoutePrompt, question: str, effort: Effort) -> RouteRequest:
    user = Template(prompt.user_template).substitute(question=question)
    return RouteRequest(system=prompt.system, user=user, effort=effort)


def read_route_reply(body: Mapping[str, Any]) -> RouteReply:
    """The class and reason in *body*; ``RouteError`` when the reply cannot be used.

    A response the API client would not accept raises ``AnswerModelError``.
    """
    reply = parse_reply(body)
    stopped = _STOPPED.get(reply.stop_reason or "")
    if stopped is not None:
        raise RouteError(f"the classifier's reply was {stopped}")
    try:
        data = json.loads(reply.text)
    except ValueError:
        data = None
    if not (isinstance(data, dict) and isinstance(data.get("reason"), str)
            and data.get("predicted_class") in PREDICTED_CLASSES):
        raise RouteError("the classifier's reply is off the schema")
    return RouteReply(cast(PredictedClass, data["predicted_class"]), data["reason"],
                      reply.usage, reply.text)


class RouteClassifier:
    """Classifies a question with one call through *model* (cached or not)."""

    def __init__(self, model: AnswerModel, prompt: RoutePrompt, effort: Effort,
                 spans: SpanRecorder | None = None):
        self._model = model
        self._prompt = prompt
        self._spans = spans if spans is not None else SpanRecorder.off()
        self.effort = effort

    @property
    def prompt(self) -> RoutePrompt:
        return self._prompt

    def classify(self, question: str) -> Classification:
        """The question's class; ``RouteError`` or ``AnswerModelError`` when it has none."""
        request = build_route_request(self._prompt, question, self.effort)
        attributes = generation_request_attributes(ROUTE_MODEL, self.effort, ROUTE_MAX_TOKENS,
                                                   self._prompt.prompt_version)
        with self._spans.span(ROUTE_CLASSIFIER_SPAN, Kind.LLM, attributes) as span:
            response = self._model.complete(request)
            reply = read_route_reply(response.body)
            cost = StageCost.anthropic(response.api_ms, ROUTE_MODEL, reply.usage,
                                       from_cache=response.from_cache, table=ROUTE_PRICES)
            messages = ((request.system, request.user, reply.text)
                        if self._spans.capture_text else None)
            span.set_attributes(classifier_attributes(reply.predicted_class, cost, reply.usage,
                                                      messages))
        return Classification(reply.predicted_class, reply.reason, self.effort,
                              self._prompt.prompt_version, self._prompt.sha256, cost)
