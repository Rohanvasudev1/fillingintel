"""The versioned Cypher prompt and its requests (Step 11 ticket 06).

The prompt file is pinned by SHA-256; its version hashes the rendered system
prompt, the user and repair templates and the output schema, as extract/ does.
"""
import json

import pytest

from graph.schema_text import schema_text
from retrieve.cypher_prompt import PromptError, load_prompt, output_schema
from retrieve.cypher_request import (
    CYPHER_EFFORT,
    CYPHER_MODEL,
    first_request,
    parse_cypher_reply,
    repair_request,
)
from retrieve.question_filter import parse_question_filter
from tests.anthropic_fixtures import load, with_text
from tests.corpus_filings import corpus_filings

FILINGS = corpus_filings()
QUESTION = "Which products does NVIDIA's Data Center platform offer in fiscal 2026?"
REPLY = {"reasoning": "Data Center offers products.", "cypher": "MATCH (n) RETURN n"}


def _body(text: str) -> dict:
    return with_text(load("answered_q0072")["response"], text)


def _filter(question=QUESTION):
    return parse_question_filter(question, FILINGS)


def test_the_system_prompt_holds_the_schema_and_every_corpus_filing():
    prompt = load_prompt(filings=FILINGS)
    assert schema_text().strip() in prompt.system
    for f in FILINGS:
        assert f"| {f.ticker} | {f.form_type} | {f.fiscal_period} | {f.accession_no} |" \
            in prompt.system
    assert "$" not in prompt.system.replace("$name", "")


def test_the_version_names_the_file_and_hashes_what_is_sent():
    prompt = load_prompt(filings=FILINGS)
    assert prompt.prompt_version == f"cypher/v1@{prompt.sha256[:8]}"
    assert prompt.path == "prompts/cypher/v1.md"
    fewer = load_prompt(filings=FILINGS[:3])
    assert fewer.file_sha256 == prompt.file_sha256
    assert fewer.sha256 != prompt.sha256  # another filing list is another prompt


def test_a_changed_file_is_refused(tmp_path):
    (tmp_path / "v1.md").write_text("# System\nx\n# User\n$question $filter\n# Repair\n"
                                    "$query $message\n", encoding="utf-8")
    with pytest.raises(PromptError, match="changed since it was published"):
        load_prompt(filings=FILINGS, prompts_dir=tmp_path)


def test_an_unknown_version_is_refused():
    with pytest.raises(PromptError, match="not published"):
        load_prompt("v9", filings=FILINGS)


def test_the_first_request_sends_the_question_and_its_parsed_filter():
    request = first_request(load_prompt(filings=FILINGS), QUESTION, _filter())
    params = request.params()
    assert params["model"] == CYPHER_MODEL == "claude-sonnet-5-5"
    assert params["output_config"] == {
        "effort": CYPHER_EFFORT, "format": {"type": "json_schema", "schema": output_schema()}}
    assert CYPHER_EFFORT == "high"
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    (message,) = params["messages"]
    assert message["role"] == "user"
    assert QUESTION in message["content"]
    assert "companies NVDA; periods FY2026" in message["content"]
    assert "0001045810-26-000021" in message["content"]  # NVIDIA's fiscal 2026 10-K
    assert not {"temperature", "top_p", "top_k", "tools"} & set(params)


def test_a_question_with_no_filter_says_every_filing():
    request = first_request(load_prompt(filings=FILINGS), "What risks are named?",
                            _filter("What risks are named?"))
    assert "none; every filing is in scope" in request.params()["messages"][0]["content"]


def test_the_output_schema_asks_for_reasoning_then_cypher():
    schema = output_schema()
    assert list(schema["properties"]) == ["reasoning", "cypher"]
    assert schema["required"] == ["reasoning", "cypher"]
    assert schema["additionalProperties"] is False


def test_a_repair_resends_the_conversation_with_the_failed_query_and_message():
    prompt = load_prompt(filings=FILINGS)
    first = first_request(prompt, QUESTION, _filter())
    reply_text = json.dumps(REPLY)
    repair = repair_request(prompt, first, reply_text, "MATCH (n) RETURN n",
                            "return a column named chunk_ids")
    messages = repair.params()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0] == first.params()["messages"][0]
    assert messages[1]["content"] == reply_text
    assert "MATCH (n) RETURN n" in messages[2]["content"]
    assert "return a column named chunk_ids" in messages[2]["content"]
    assert repair.cache_key() != first.cache_key()


def test_a_reply_parses_to_reasoning_and_cypher():
    reply = parse_cypher_reply(_body(json.dumps(REPLY)))
    assert (reply.reasoning, reply.cypher) == (REPLY["reasoning"], REPLY["cypher"])
    assert reply.error is None
    assert reply.usage.output_tokens > 0


@pytest.mark.parametrize("text", ["not json", json.dumps({"reasoning": "x"}),
                                  json.dumps({"reasoning": "x", "cypher": 3}), "[]"])
def test_a_reply_without_a_query_says_why_and_keeps_its_text_and_usage(text):
    reply = parse_cypher_reply(_body(text))
    assert reply.cypher is None
    assert "reply" in reply.error
    assert reply.text == text
    assert reply.usage.output_tokens > 0


def test_a_reply_cut_off_at_max_tokens_has_no_query():
    reply = parse_cypher_reply({**_body(json.dumps(REPLY)), "stop_reason": "max_tokens"})
    assert reply.cypher is None
    assert "max_tokens" in reply.error


def test_v2_is_published_and_is_another_prompt():
    v1, v2 = load_prompt("v1", filings=FILINGS), load_prompt("v2", filings=FILINGS)
    assert v2.prompt_version.startswith("cypher/v2@")
    assert v2.sha256 != v1.sha256
    assert "CALL (f) {" in v2.system
