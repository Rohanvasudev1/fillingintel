"""The answer model, its response cache and the price table (Step 5 ticket 05).

The SDK client is a fake that replays recorded responses, so these tests check
what the code sends and how it reads what comes back, with no network.
"""
import json

import anthropic
import httpx2
import pytest

from retrieve.answer_model import (
    ANSWER_EFFORT,
    ANSWER_MAX_TOKENS,
    ANSWER_MODEL,
    AnswerModelError,
    AnswerRequest,
    AnthropicAnswerModel,
    MissingApiKey,
    parse_reply,
)
from retrieve.pricing import PRICE_TABLE_DATE, anthropic_cost, embedding_cost, price_table
from retrieve.query_cache import CacheError
from retrieve.response_cache import CachedAnswerModel
from tests.anthropic_fixtures import RECORDED, FakeAnthropicClient, load

REQUEST = AnswerRequest(
    model=ANSWER_MODEL, effort=ANSWER_EFFORT, max_tokens=ANSWER_MAX_TOKENS,
    system="You answer questions.", user="Question: What was revenue?",
)


def _model(body=None, error=None, ticks=(1.0, 3.5)):
    client = FakeAnthropicClient(body if body is not None else load(RECORDED[0])["response"], error)
    clock = iter(ticks)
    return AnthropicAnswerModel(client, clock=lambda: next(clock)), client


def test_the_pinned_answer_model_and_effort():
    assert (ANSWER_MODEL, ANSWER_EFFORT) == ("claude-sonnet-5-5", "high")


def test_the_request_sets_effort_and_sends_no_sampling_settings():
    model, client = _model()
    model.complete(REQUEST)
    (sent,) = client.calls
    assert sent == {
        "model": "claude-sonnet-5-5",
        "max_tokens": ANSWER_MAX_TOKENS,
        "system": "You answer questions.",
        "messages": [{"role": "user", "content": "Question: What was revenue?"}],
        "output_config": {"effort": "high"},
    }
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(sent)


def test_the_response_body_and_api_latency_come_back():
    model, _ = _model()
    response = model.complete(REQUEST)
    assert response.body == load(RECORDED[0])["response"]
    assert response.api_ms == pytest.approx(2500.0)
    assert response.from_cache is False


@pytest.mark.parametrize("name", RECORDED)
def test_every_recorded_response_parses(name):
    reply = parse_reply(load(name)["response"])
    assert reply.text.startswith("STATUS: ")
    assert reply.stop_reason == "end_turn"
    assert reply.model == ANSWER_MODEL
    assert reply.usage.input_tokens > 0 and reply.usage.output_tokens > 0


def test_only_text_blocks_make_the_reply_text():
    body = load(RECORDED[0])["response"]
    assert body["content"][0]["type"] == "thinking"
    assert parse_reply(body).text == body["content"][1]["text"]


def test_a_refusal_keeps_its_stop_reason_and_category():
    body = {**load(RECORDED[0])["response"], "content": [], "stop_reason": "refusal",
            "stop_details": {"type": "refusal", "category": "cyber", "explanation": None}}
    reply = parse_reply(body)
    assert (reply.text, reply.stop_reason, reply.refusal_category) == ("", "refusal", "cyber")


def test_a_malformed_body_is_an_answer_model_error():
    with pytest.raises(AnswerModelError, match="unexpected response shape"):
        parse_reply({"content": "not a list"})


def test_an_api_error_is_an_answer_model_error_with_its_type_and_status():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(529, request=request)
    error = anthropic.APIStatusError("overloaded", response=response, body=None)
    model, _ = _model(error=error)
    with pytest.raises(AnswerModelError, match="APIStatusError.*529"):
        model.complete(REQUEST)


def test_a_missing_key_is_refused_and_the_key_never_shows(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingApiKey, match="ANTHROPIC_API_KEY"):
        AnthropicAnswerModel.from_env()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-a-real-key")
    with AnthropicAnswerModel.from_env() as model:
        assert "sk-not" not in repr(model)


def test_the_cache_key_covers_model_effort_and_the_whole_prompt():
    keys = {
        REQUEST.cache_key(),
        AnswerRequest(**{**REQUEST.__dict__, "effort": "medium"}).cache_key(),
        AnswerRequest(**{**REQUEST.__dict__, "user": "Question: other"}).cache_key(),
        AnswerRequest(**{**REQUEST.__dict__, "system": "Other."}).cache_key(),
        AnswerRequest(**{**REQUEST.__dict__, "max_tokens": 8000}).cache_key(),
    }
    assert len(keys) == 5


def test_a_rerun_is_served_from_the_cache_with_the_recorded_latency(tmp_path):
    inner, client = _model()
    cached = CachedAnswerModel(inner, tmp_path)
    first = cached.complete(REQUEST)
    second = cached.complete(REQUEST)
    assert len(client.calls) == 1
    assert (first.from_cache, second.from_cache) == (False, True)
    assert second.body == first.body
    assert second.api_ms == first.api_ms == pytest.approx(2500.0)
    (path,) = tmp_path.rglob("*.json")
    assert path.relative_to(tmp_path).parts[:2] == (ANSWER_MODEL, ANSWER_EFFORT)


def test_a_different_effort_is_a_cache_miss(tmp_path):
    inner, client = _model(ticks=(1.0, 2.0, 3.0, 4.0))
    cached = CachedAnswerModel(inner, tmp_path)
    cached.complete(REQUEST)
    cached.complete(AnswerRequest(**{**REQUEST.__dict__, "effort": "medium"}))
    assert len(client.calls) == 2


def test_a_damaged_cache_file_is_an_error_not_a_miss(tmp_path):
    inner, _ = _model()
    cached = CachedAnswerModel(inner, tmp_path)
    cached.complete(REQUEST)
    (path,) = tmp_path.rglob("*.json")
    stored = json.loads(path.read_text())
    path.write_text(json.dumps({**stored, "key": "0" * 64}))
    with pytest.raises(CacheError, match="another request"):
        cached.complete(REQUEST)
    path.write_text("{not json")
    with pytest.raises(CacheError, match="cannot read"):
        cached.complete(REQUEST)


def test_cost_comes_from_reported_tokens_and_the_dated_price_table():
    usage = parse_reply(load(RECORDED[0])["response"]).usage
    assert anthropic_cost(ANSWER_MODEL, usage) == pytest.approx(
        (31840 * 2.00 + 812 * 10.00) / 1_000_000
    )
    assert embedding_cost("voyage-4-large", 25) == pytest.approx(25 * 0.12 / 1_000_000)
    assert PRICE_TABLE_DATE == "2026-10-05"


def test_cache_reads_and_writes_are_priced_at_their_own_rates():
    usage = parse_reply(load(RECORDED[0])["response"]).usage
    usage = usage.__class__(input_tokens=1000, output_tokens=0,
                            cache_read_input_tokens=2000, cache_creation_input_tokens=400)
    assert anthropic_cost(ANSWER_MODEL, usage) == pytest.approx(
        (1000 * 2.00 + 2000 * 0.20 + 400 * 2.50) / 1_000_000
    )


def test_an_unpriced_model_is_refused():
    with pytest.raises(ValueError, match="no price"):
        embedding_cost("voyage-unknown", 10)


def test_the_price_table_for_the_header_names_its_date_and_sources():
    table = price_table([ANSWER_MODEL, "voyage-4-large"])
    assert table["date"] == PRICE_TABLE_DATE
    assert table["models"][ANSWER_MODEL]["input_per_mtok"] == 2.00
    assert table["models"][ANSWER_MODEL]["source"].startswith("https://")
    assert set(table["models"]) == {ANSWER_MODEL, "voyage-4-large"}
