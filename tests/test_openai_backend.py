"""The OpenAI Responses API backend, its reply parser and its cost (Step 5, ticket 09).

The fake client replays recorded ``gpt-6-luna`` bodies; no test touches the network.
"""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx2
import openai
import pytest
from openai.types.responses import Response

from eval.judging.openai_backend import (
    API_BASE_URL,
    API_KEY_ENV,
    BASE_URL_ENV,
    OpenAIResponsesModel,
    OpenAIUsage,
    parse_openai_reply,
)
from retrieve.answer_model import AnswerModelError
from retrieve.pricing import PRICES, openai_cost, price_table
from tests.openai_fixtures import RECORDED, incomplete, with_refusal, with_text

REPO_ROOT = Path(__file__).resolve().parents[1]
PRICING_URL = "https://developers.openai.com/api/docs/pricing"


class _Request:
    model = "gpt-6-luna"
    effort = "medium"

    def params(self):
        return {"model": self.model, "input": [{"role": "user", "content": "p"}], "store": False}

    def cache_key(self):
        return "0" * 64


class _FakeClient:
    def __init__(self, body=None, error=None):
        self.calls = []
        self.closed = False
        self._body, self._error = body, error
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return Response.model_validate(self._body)

    def close(self):
        self.closed = True


def _ticks(*values):
    it = iter(values)
    return lambda: next(it)


def test_complete_sends_the_params_and_returns_the_body_with_its_latency():
    client = _FakeClient(RECORDED)
    response = OpenAIResponsesModel(client, clock=_ticks(1.0, 3.5)).complete(_Request())
    assert client.calls == [_Request().params()]
    assert response.body["id"] == RECORDED["id"]
    assert response.api_ms == pytest.approx(2500.0)
    assert response.from_cache is False


def test_the_returned_body_keeps_the_apis_field_names_so_it_parses_again():
    # The SDK names the request format's ``schema`` field ``schema_`` in Python.
    response = OpenAIResponsesModel(_FakeClient(RECORDED)).complete(_Request())
    assert "schema" in response.body["text"]["format"]
    assert "schema_" not in response.body["text"]["format"]
    assert parse_openai_reply(response.body).status == "completed"


def test_an_api_failure_is_an_answer_model_error_that_stops_the_run():
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    error = openai.APIStatusError("overloaded", response=httpx2.Response(503, request=request),
                                  body=None)
    with pytest.raises(AnswerModelError, match="APIStatusError.*503"):
        OpenAIResponsesModel(_FakeClient(error=error)).complete(_Request())
    network = openai.APIConnectionError(request=request)
    with pytest.raises(AnswerModelError, match="APIConnectionError"):
        OpenAIResponsesModel(_FakeClient(error=network)).complete(_Request())


@pytest.mark.parametrize(("status", "error_class"), [
    (401, openai.AuthenticationError), (403, openai.PermissionDeniedError)])
def test_an_auth_failure_never_quotes_the_apis_message_which_echoes_the_key(status, error_class):
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    message = "Incorrect API key provided: sk-proj-****************abcd."
    error = error_class(message, response=httpx2.Response(status, request=request), body=None)
    with pytest.raises(AnswerModelError, match=f"HTTP {status}") as raised:
        OpenAIResponsesModel(_FakeClient(error=error)).complete(_Request())
    assert "sk-" not in str(raised.value) and "abcd" not in str(raised.value)
    assert "OPENAI_API_KEY" in str(raised.value)


def test_a_missing_key_is_refused_and_the_key_never_shows(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv(BASE_URL_ENV, raising=False)
    with pytest.raises(AnswerModelError, match="OPENAI_API_KEY"):
        OpenAIResponsesModel.from_env()
    monkeypatch.setenv(API_KEY_ENV, "sk-not-a-real-key")
    with OpenAIResponsesModel.from_env() as model:
        assert "sk-not" not in repr(model)
        assert str(model._client.base_url) == API_BASE_URL + "/"


def test_a_set_base_url_is_refused_so_the_key_cannot_go_elsewhere(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "sk-not-a-real-key")
    monkeypatch.setenv(BASE_URL_ENV, "https://example.invalid/v1")
    with pytest.raises(AnswerModelError, match="OPENAI_BASE_URL") as raised:
        OpenAIResponsesModel.from_env()
    assert "sk-not" not in str(raised.value)
    assert "example.invalid" not in str(raised.value)


def test_the_reply_parser_reads_text_status_and_usage():
    reply = parse_openai_reply(RECORDED)
    reported = RECORDED["usage"]
    assert reply.status == "completed" and reply.refusal is None
    assert reply.model == RECORDED["model"]
    assert '"correct"' in reply.text
    assert reply.usage == OpenAIUsage(
        input_tokens=reported["input_tokens"],
        cached_input_tokens=reported["input_tokens_details"]["cached_tokens"],
        cache_write_tokens=reported["input_tokens_details"]["cache_write_tokens"],
        output_tokens=reported["output_tokens"],
        reasoning_tokens=reported["output_tokens_details"]["reasoning_tokens"],
    )


def test_the_reply_parser_reports_refusals_and_incomplete_replies():
    refused = parse_openai_reply(with_refusal(RECORDED, "No."))
    assert refused.refusal == "No." and refused.text == ""
    stopped = parse_openai_reply(incomplete(RECORDED))
    assert stopped.status == "incomplete"
    assert stopped.incomplete_reason == "max_output_tokens"
    assert parse_openai_reply(with_text(RECORDED, '{"a": 1}')).text == '{"a": 1}'


def test_an_unexpected_response_shape_is_an_answer_model_error():
    with pytest.raises(AnswerModelError, match="unexpected response shape"):
        parse_openai_reply({"id": "resp_x"})


def test_openai_cost_bills_cached_input_at_the_cached_rate_and_reasoning_as_output():
    usage = OpenAIUsage(input_tokens=10_000, cached_input_tokens=4_000, cache_write_tokens=1_000,
                        output_tokens=3_000, reasoning_tokens=2_500)
    # 5,000 plain input at 0.10, 4,000 cached at 0.01, 1,000 written at 0.125,
    # 3,000 output (reasoning included) at 0.50; per million tokens.
    assert openai_cost("gpt-6-luna", usage) == pytest.approx(
        (5_000 * 0.10 + 4_000 * 0.01 + 1_000 * 0.125 + 3_000 * 0.50) / 1_000_000)
    assert openai_cost("gpt-6-sol", usage) == pytest.approx(
        (5_000 * 2.00 + 4_000 * 0.20 + 1_000 * 2.50 + 3_000 * 10.00) / 1_000_000)


def test_luna_and_sol_have_dated_price_rows_from_openais_pricing_page():
    table = price_table(["gpt-6-luna", "gpt-6-sol"])
    assert table["date"] == "2026-10-05"
    assert table["models"]["gpt-6-luna"]["source"] == PRICING_URL
    assert table["models"]["gpt-6-sol"]["source"] == PRICING_URL
    assert "claude-opus-5-5" not in PRICES


def test_openai_imports_and_builds_a_client_with_the_network_blocked():
    script = """
import socket

def _blocked(*args, **kwargs):
    raise RuntimeError("network access attempted")

socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked

import openai
from eval.judging.openai_backend import OpenAIResponsesModel
with OpenAIResponsesModel.from_env():
    pass
print("ok")
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENAI_")}
    env["OPENAI_API_KEY"] = "sk-not-a-real-key"
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-3000:]
    assert result.stdout.strip() == "ok"
