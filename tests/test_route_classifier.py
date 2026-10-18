"""The router's classifier, pure (Step 13 ticket 03).

The prompt is frozen by SHA-256 and its version covers the system prompt, user
template and output schema; the request is claude-haiku-5-5 with structured
output, the effort asked for and no sampling settings; a reply cut off,
refused or off the schema is an error, never a default class.
"""
import json
import shutil

import pytest

from retrieve.answer_model import ApiResponse
from retrieve.route_classifier import (
    PREDICTED_CLASSES,
    ROUTE_MODEL,
    RouteClassifier,
    RouteError,
    build_route_request,
    read_route_reply,
)
from retrieve.route_prompt import ROUTE_PROMPTS_DIR, RoutePromptError, load_route_prompt
from tests.anthropic_fixtures import load, with_text

QUESTION = "What was Intel's capital expenditure guidance in its latest 10-Q?"


def _body(reply: object, **changes) -> dict:
    text = reply if isinstance(reply, str) else json.dumps(reply)
    return {**with_text(load("answered_q0072")["response"], text), **changes}


class OneReply:
    def __init__(self, body: dict, from_cache: bool = False):
        self._body = body
        self._from_cache = from_cache
        self.requests: list = []

    def complete(self, request) -> ApiResponse:
        self.requests.append(request)
        return ApiResponse(self._body, api_ms=420.0, from_cache=self._from_cache)


# ── prompt ────────────────────────────────────────────────────────────────────

def test_v1_loads_with_a_version_that_covers_what_is_sent():
    prompt = load_route_prompt("v1")
    assert prompt.prompt_version == f"route/v1@{prompt.sha256[:8]}"
    assert prompt.path == "prompts/route/v1.md"
    assert "# User" not in prompt.system and "$question" in prompt.user_template
    for label in PREDICTED_CLASSES:
        assert f"-> {label}" in prompt.system  # at least one example per label


def test_an_edited_prompt_is_refused(tmp_path):
    shutil.copy(ROUTE_PROMPTS_DIR / "v1.md", tmp_path / "v1.md")
    with open(tmp_path / "v1.md", "a", encoding="utf-8") as fh:
        fh.write("Prefer lookup.\n")
    with pytest.raises(RoutePromptError, match="route v1 has changed"):
        load_route_prompt("v1", tmp_path)


def test_an_unpublished_version_is_refused():
    with pytest.raises(RoutePromptError, match="not published"):
        load_route_prompt("v9")


# ── request ───────────────────────────────────────────────────────────────────

def test_the_request_is_haiku_with_the_effort_and_a_reason_first_enum_schema():
    prompt = load_route_prompt("v1")
    params = build_route_request(prompt, QUESTION, "medium").params()
    assert params["model"] == ROUTE_MODEL == "claude-haiku-5-5"
    assert params["output_config"]["effort"] == "medium"
    schema = params["output_config"]["format"]["schema"]
    assert list(schema["properties"]) == ["reason", "predicted_class"]
    assert schema["properties"]["predicted_class"]["enum"] == list(PREDICTED_CLASSES)
    assert set(PREDICTED_CLASSES) == {"lookup", "local", "multi_hop", "global", "decline"}
    assert params["system"][0]["text"] == prompt.system
    assert params["messages"] == [{"role": "user", "content": f"Question: {QUESTION}"}]
    assert not {"temperature", "top_p", "top_k", "tools"} & set(params)


def test_requests_differ_by_effort_and_question_only():
    prompt = load_route_prompt("v1")
    low = build_route_request(prompt, QUESTION, "low")
    assert low.cache_key() == build_route_request(prompt, QUESTION, "low").cache_key()
    assert low.cache_key() != build_route_request(prompt, QUESTION, "high").cache_key()
    assert low.cache_key() != build_route_request(prompt, QUESTION + " ", "low").cache_key()


# ── reply ─────────────────────────────────────────────────────────────────────

def test_a_reply_gives_its_class_reason_and_usage():
    reply = read_route_reply(_body({"reason": "One reported figure.",
                                    "predicted_class": "lookup"}))
    assert (reply.predicted_class, reply.reason) == ("lookup", "One reported figure.")
    assert reply.usage.output_tokens > 0


@pytest.mark.parametrize(("body", "match"), [
    (_body({"reason": "x", "predicted_class": "unanswerable"}), "off the schema"),
    (_body({"reason": "x"}), "off the schema"),
    (_body("lookup"), "off the schema"),
    (_body({"reason": "x", "predicted_class": "lookup"}, stop_reason="max_tokens"), "cut off"),
    (_body("", stop_reason="refusal"), "refused"),
])
def test_an_unusable_reply_is_an_error(body, match):
    with pytest.raises(RouteError, match=match):
        read_route_reply(body)


# ── classifier ────────────────────────────────────────────────────────────────

def test_the_classifier_records_cost_latency_and_prompt():
    model = OneReply(_body({"reason": "Advice.", "predicted_class": "decline"}),
                     from_cache=True)
    classifier = RouteClassifier(model, load_route_prompt("v1"), "low")
    found = classifier.classify(QUESTION)
    assert found.predicted_class == "decline" and found.reason == "Advice."
    assert found.effort == "low"
    assert found.prompt_version.startswith("route/v1@")
    assert found.cost.ms == 420.0 and found.cost.from_cache
    assert found.cost.cost_usd > 0 and found.cost.output_tokens > 0
    assert model.requests[0].params()["output_config"]["effort"] == "low"
