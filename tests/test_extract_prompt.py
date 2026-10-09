"""The versioned extract prompt and the request built from it (Step 8)."""
import json
import re
import shutil

import pytest

from extract.filers import FilingInfo
from extract.output_schema import output_schema
from extract.prompt import PROMPTS_DIR, PromptError, load_prompt
from extract.request import EXTRACT_MAX_TOKENS, EXTRACT_MODEL, ChunkInput, build_request
from graph.schema_text import schema_text
from retrieve.answer_model import AnthropicAnswerModel
from tests.anthropic_fixtures import FakeAnthropicClient, load, with_text
from tests.graph_test_data import NVDA_10K

FILING = FilingInfo(cik="1045810", accession_no=NVDA_10K, company_name="NVIDIA CORP",
                    form_type="10-K", fiscal_period="FY2026")
CHUNK = ChunkInput(f"{NVDA_10K}:0012", "part_i_item_1", "We utilize foundries, such as TSMC.")
SAMPLING = {"temperature", "top_p", "top_k", "tools", "tool_choice"}


def test_prompt_version_names_the_file_version_and_hash():
    prompt = load_prompt("v1")
    assert prompt.version == "v1"
    assert re.fullmatch(r"extract/v1@[0-9a-f]{8}", prompt.prompt_version)
    assert prompt.prompt_version == f"extract/v1@{prompt.sha256[:8]}"


def test_prompt_carries_the_generated_schema_text_and_filer_references():
    system = load_prompt().system
    assert schema_text() in system
    for reference in ("THIS_FILING", "FILER", "NVDA", "AMD", "INTC"):
        assert reference in system


def test_unknown_version_is_refused():
    with pytest.raises(PromptError, match="not published"):
        load_prompt("v99")


def test_an_edited_prompt_file_is_refused(tmp_path):
    shutil.copy(PROMPTS_DIR / "v1.md", tmp_path / "v1.md")
    with (tmp_path / "v1.md").open("a", encoding="utf-8") as fh:
        fh.write("\nOne more rule.\n")
    with pytest.raises(PromptError, match="changed"):
        load_prompt("v1", tmp_path)


def test_a_user_template_with_an_unknown_placeholder_is_refused(tmp_path, monkeypatch):
    import hashlib

    from extract import prompt as prompt_module
    raw = (PROMPTS_DIR / "v1.md").read_text(encoding="utf-8") + "$unknown\n"
    (tmp_path / "v1.md").write_text(raw, encoding="utf-8")
    pinned = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    monkeypatch.setattr(prompt_module, "PUBLISHED", {"v1": pinned})
    with pytest.raises(PromptError, match="user"):
        load_prompt("v1", tmp_path)


def test_request_parameters():
    params = build_request(load_prompt(), FILING, CHUNK).params()
    assert params["model"] == EXTRACT_MODEL == "claude-sonnet-5-5"
    assert params["max_tokens"] == EXTRACT_MAX_TOKENS == 8_000
    assert params["output_config"] == {
        "effort": "high",
        "format": {"type": "json_schema", "schema": output_schema()},
    }
    assert not SAMPLING & set(params)


def test_one_cache_breakpoint_at_the_end_of_the_system_prompt():
    params = build_request(load_prompt(), FILING, CHUNK).params()
    system = params["system"]
    assert [b.get("cache_control") for b in system] == [{"type": "ephemeral"}]
    assert system[0]["text"] == load_prompt().system
    assert "cache_control" not in json.dumps(params["messages"])


def test_user_message_holds_the_chunk_and_filer():
    user = build_request(load_prompt(), FILING, CHUNK).params()["messages"]
    assert [m["role"] for m in user] == ["user"]
    assert CHUNK.text in user[0]["content"]
    assert "NVIDIA CORP" in user[0]["content"] and "FY2026" in user[0]["content"]


def test_cache_key_covers_the_chunk_text():
    prompt = load_prompt()
    key = build_request(prompt, FILING, CHUNK).cache_key()
    assert key == build_request(prompt, FILING, CHUNK).cache_key()
    other = ChunkInput(CHUNK.chunk_id, CHUNK.section, CHUNK.text + " ")
    assert key != build_request(prompt, FILING, other).cache_key()


def test_request_goes_through_the_existing_anthropic_client():
    client = FakeAnthropicClient(with_text(load("answered_q0072")["response"], "{}"))
    request = build_request(load_prompt(), FILING, CHUNK)
    AnthropicAnswerModel(client).complete(request)
    assert client.calls == [request.params()]


def test_filing_info_refuses_a_cik_outside_the_corpus():
    with pytest.raises(ValueError, match="not one of the three filers"):
        FilingInfo(cik="320193", accession_no=NVDA_10K, company_name="APPLE INC",
                   form_type="10-K", fiscal_period="FY2026")
