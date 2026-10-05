"""The versioned answer prompt (Step 5 ticket 05): published versions are frozen by
their SHA-256, and rendering puts every retrieved chunk and the question in the
user message."""
import hashlib
import shutil

import pytest

from retrieve.answer_prompt import (
    PROMPTS_DIR,
    PromptError,
    SourceChunk,
    load_prompt,
    render_user,
)

CHUNKS = (
    SourceChunk("0001045810-26-000021:0067", "NVDA", "10-K", "FY2026", "part_ii_item_7",
                "Sales to one direct customer represented 22% of total revenue."),
    SourceChunk("0000002488-25-000012:0107", "AMD", "10-Q", "FY2025-Q3", "part_i_item_2",
                "Data Center segment revenue was $4.3 billion."),
)


def test_v1_loads_with_its_version_hash_and_both_parts():
    prompt = load_prompt("v1")
    raw = (PROMPTS_DIR / "v1.md").read_bytes()
    assert prompt.version == "v1"
    assert prompt.sha256 == hashlib.sha256(raw).hexdigest()
    assert prompt.path == "prompts/answer/v1.md"
    assert prompt.system.startswith("You answer questions about the SEC 10-K and 10-Q")
    assert "# User" not in prompt.system
    assert "$excerpts" in prompt.user_template and "$question" in prompt.user_template


def test_v1_asks_for_citations_declines_advice_and_reports_missing_evidence():
    system = load_prompt("v1").system
    assert "STATUS: answered" in system
    assert "in square brackets" in system
    assert "STATUS: declined" in system and "buy, hold or sell" in system
    assert "price target" in system
    assert "STATUS: not_found" in system


def test_an_edited_published_version_is_refused(tmp_path):
    shutil.copy(PROMPTS_DIR / "v1.md", tmp_path / "v1.md")
    with open(tmp_path / "v1.md", "a", encoding="utf-8") as fh:
        fh.write("Be brief.\n")
    with pytest.raises(PromptError, match="v1 has changed"):
        load_prompt("v1", tmp_path)


def test_an_unpublished_version_is_refused():
    with pytest.raises(PromptError, match="v99"):
        load_prompt("v99")


def test_the_user_message_holds_every_chunk_with_its_source_and_the_question():
    question = "What share of NVIDIA's revenue came from its largest customer?"
    user = render_user(load_prompt("v1"), question, CHUNKS)
    assert user.index(CHUNKS[0].chunk_id) < user.index(CHUNKS[1].chunk_id)
    assert (
        '<excerpt id="0001045810-26-000021:0067" company="NVDA" form="10-K" '
        'period="FY2026" section="part_ii_item_7">\n'
        "Sales to one direct customer represented 22% of total revenue.\n</excerpt>"
    ) in user
    assert user.endswith(f"Question: {question}")


def test_dollar_signs_in_the_question_or_chunks_are_left_alone():
    user = render_user(load_prompt("v1"), "Was revenue above $question billion?", CHUNKS)
    assert user.endswith("Question: Was revenue above $question billion?")
    assert "$4.3 billion" in user
