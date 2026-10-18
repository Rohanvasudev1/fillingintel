"""The claims split for calibration (Step 13 ticket 04): prompt, request, reply and command.

Answers are real dev answers from the committed run files; the model is a fake that
returns a recorded Anthropic response body with the reply text swapped in.
"""
import json

import pytest

from eval.calibration import claims as claims_module
from eval.calibration.answers import RUNS_DIR, load_answers
from eval.calibration.claims import (
    CLAIMS_EFFORT,
    CLAIMS_MODEL,
    ClaimsError,
    ClaimsPromptError,
    build_claims_request,
    load_claims_prompt,
    read_claims_reply,
)
from eval.calibration.draw import CALIBRATION_SEED, draw, write_draw
from retrieve.answer_model import ApiResponse
from tests.anthropic_fixtures import load, with_text

BODY = load("answered_q0072")["response"]


@pytest.fixture(scope="module")
def answer():
    return next(a for a in load_answers(RUNS_DIR) if a.kind == "answer" and len(a.kept) >= 2)


def _reply(claims, stop_reason="end_turn"):
    return {**with_text(BODY, json.dumps({"claims": claims})), "stop_reason": stop_reason}


def test_prompt_is_pinned(tmp_path):
    prompt = load_claims_prompt()
    assert prompt.prompt_version.startswith("claims/v1@")
    changed = tmp_path / "v1.md"
    changed.write_text("# System\nx\n# User\n$question $sentences\n")
    with pytest.raises(ClaimsPromptError, match="changed"):
        load_claims_prompt(prompts_dir=tmp_path)


def test_request_sends_sentences_without_citations_and_no_sampling(answer):
    request = build_claims_request(load_claims_prompt(), answer)
    params = request.params()
    assert params["model"] == CLAIMS_MODEL == "claude-sonnet-5-5"
    assert params["output_config"]["effort"] == CLAIMS_EFFORT == "high"
    assert not {"temperature", "top_p", "top_k", "tools"} & set(params)
    user = params["messages"][0]["content"]
    assert answer.question in user
    assert "1. " in user and "2. " in user
    assert "[" + answer.kept[0].citations[0] not in user, "citations are stripped"
    schema = params["output_config"]["format"]["schema"]
    assert schema["properties"]["claims"]["items"]["required"] == ["sentence_index", "text"]


def test_reply_parses_claims_in_sentence_range(answer):
    claims = read_claims_reply(_reply([{"sentence_index": 1, "text": "A."},
                                       {"sentence_index": 2, "text": "B."}]),
                               len(answer.kept))
    assert [(c.sentence_index, c.text) for c in claims.claims] == [(1, "A."), (2, "B.")]


@pytest.mark.parametrize(("claims", "stop", "match"), [
    ([{"sentence_index": 9, "text": "A."}], "end_turn", "sentence 9"),
    ([{"sentence_index": 1, "text": " "}], "end_turn", "empty"),
    ([], "end_turn", "no claims"),
    ([{"sentence_index": 1, "text": "A."}], "max_tokens", "cut off"),
])
def test_reply_that_cannot_be_used_is_an_error(claims, stop, match):
    with pytest.raises(ClaimsError, match=match):
        read_claims_reply(_reply(claims, stop), 2)


class _Model:
    def __init__(self):
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        n = request.user.count("\n") - 2  # the numbered sentence lines
        return ApiResponse(_reply([{"sentence_index": i, "text": f"claim {i}"}
                                   for i in range(1, max(n, 1) + 1)]), 10.0)


def _setup(tmp_path):
    pool = load_answers(RUNS_DIR)
    draw_path = tmp_path / "draw.json"
    write_draw(draw_path, draw(pool, CALIBRATION_SEED), CALIBRATION_SEED, "abc")
    return draw_path


def test_command_writes_one_line_per_answer_and_refuses_to_overwrite(tmp_path):
    draw_path = _setup(tmp_path)
    out = tmp_path / "claims.jsonl"
    model = _Model()
    args = ["--draw", str(draw_path), "--output", str(out)]
    assert claims_module.main(args, response_cache=tmp_path / "cache", model=model,
                              spent=lambda: 0.0) == 0
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(lines) == 24 == len(model.requests), "answers only, not behaviour records"
    assert all(line["claims"] and line["prompt"].startswith("claims/v1@") for line in lines)
    assert all(line["spent_usd"] > 0 for line in lines)
    assert claims_module.main(args, response_cache=tmp_path / "cache", model=model,
                              spent=lambda: 0.0) == 2, "write-once"


def test_command_refuses_above_the_cap_before_any_call(tmp_path):
    draw_path = _setup(tmp_path)
    model = _Model()
    code = claims_module.main(["--draw", str(draw_path), "--output",
                               str(tmp_path / "c.jsonl"), "--max-cost", "0.0001"],
                              response_cache=tmp_path / "cache", model=model,
                              spent=lambda: 0.0)
    assert code == 2 and not model.requests
    assert claims_module.main(["--draw", str(draw_path), "--output", str(tmp_path / "d.jsonl")],
                              response_cache=tmp_path / "cache", model=model,
                              spent=lambda: 9.99) == 2, "Step 13 cap"
