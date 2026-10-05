"""Writing a cited answer from retrieved chunks (Step 5 ticket 05).

A fake answer model replays the recorded responses in ``tests/fixtures/anthropic/``:
an answer, a decline and a not-found answer.  The uncited-sentence case is built
in the test from the recorded answer, so the edit is visible here.
"""
import re

import pytest

from retrieve.answer import DECLINE_TEXT, NOT_FOUND_TEXT, write_answer
from retrieve.answer_model import ANSWER_EFFORT, ANSWER_MODEL, AnswerRequest, ApiResponse
from retrieve.answer_prompt import SourceChunk, load_prompt
from retrieve.citations import split_sentences
from retrieve.pricing import anthropic_cost
from tests.anthropic_fixtures import load, with_text

NOT_RETRIEVED = "0000050863-25-000010:0005"
CITATION = re.compile(r"\s*\[[^\]]*\]")


class ReplayAnswerModel:
    def __init__(self, body: dict, api_ms: float = 9000.0, from_cache: bool = False):
        self._response = ApiResponse(body, api_ms, from_cache)
        self.requests: list[AnswerRequest] = []

    def complete(self, request: AnswerRequest) -> ApiResponse:
        self.requests.append(request)
        return self._response


def _chunks(fixture: dict) -> tuple[SourceChunk, ...]:
    return tuple(
        SourceChunk(cid, "NVDA", "10-K", "FY2026", "part_ii_item_7", f"text of {cid}")
        for cid in fixture["retrieved_chunk_ids"]
    )


def _answer(fixture: dict, body: dict | None = None, **model_kwargs):
    model = ReplayAnswerModel(body or fixture["response"], fixture["api_ms"], **model_kwargs)
    answer = write_answer(fixture["question"], _chunks(fixture), model, load_prompt("v1"))
    return answer, model


def test_the_request_holds_every_retrieved_chunk_and_the_pinned_model_and_effort():
    fixture = load("answered_q0072")
    _, model = _answer(fixture)
    (request,) = model.requests
    assert (request.model, request.effort) == (ANSWER_MODEL, ANSWER_EFFORT)
    assert request.system == load_prompt("v1").system
    assert all(cid in request.user for cid in fixture["retrieved_chunk_ids"])
    assert len(fixture["retrieved_chunk_ids"]) == 10
    assert request.user.endswith(f"Question: {fixture['question']}")


def test_a_fully_cited_answer_passes_through_with_its_counts_cost_and_latency():
    fixture = load("answered_q0072")
    answer, _ = _answer(fixture)
    body = fixture["response"]["content"][-1]["text"]
    assert answer.status == "answered"
    assert answer.text == body.removeprefix("STATUS: answered\n")
    assert len(answer.citation_check.kept) == 3 and answer.citation_check.dropped == ()
    assert (answer.citation_check.citations, answer.citation_check.citations_retrieved) == (3, 3)
    assert answer.raw_text == body
    assert answer.cost_usd == pytest.approx(anthropic_cost(ANSWER_MODEL, answer.usage))
    assert answer.generation_ms == fixture["api_ms"]
    assert (answer.model, answer.stop_reason, answer.from_cache) == (ANSWER_MODEL, "end_turn",
                                                                     False)


def test_uncited_sentences_and_citations_outside_the_retrieved_chunks_are_dropped():
    fixture = load("answered_q0072")
    recorded = fixture["response"]["content"][-1]["text"].removeprefix("STATUS: answered\n")
    first, second, *rest = (s.text for s in split_sentences(recorded))
    uncited = CITATION.sub("", second)  # the second sentence with its citations removed
    foreign = f"Intel's share was lower [{NOT_RETRIEVED}]."
    text = " ".join(["STATUS: answered\n" + first, uncited, *rest, foreign])
    answer, _ = _answer(fixture, with_text(fixture["response"], text))
    check = answer.citation_check
    assert [(d.text, d.reason) for d in check.dropped] == [
        (uncited, "no_citation"),
        (foreign, "citation_not_retrieved"),
    ]
    assert answer.text == " ".join([first, *rest])
    assert check.sentences == 2 + len(rest) + 1
    assert check.citations - check.citations_retrieved == 1
    assert uncited in answer.raw_text


def test_a_decline_passes_through_as_the_fixed_decline_text():
    answer, _ = _answer(load("declined_q0005"))
    assert answer.status == "declined"
    assert answer.text == DECLINE_TEXT
    assert answer.citation_check.sentences == 0


def test_a_not_found_answer_keeps_its_cited_context_after_the_fixed_text():
    fixture = load("not_found_q0011")
    answer, _ = _answer(fixture)
    (kept,) = answer.citation_check.kept
    assert answer.status == "not_found"
    assert answer.text == f"{NOT_FOUND_TEXT} {kept.text}"
    assert answer.citation_check.dropped == ()


def test_an_uncited_sentence_after_a_decline_is_dropped_and_counted():
    fixture = load("declined_q0005")
    body = with_text(fixture["response"], "STATUS: declined\nAMD looks cheap to me.")
    answer, _ = _answer(fixture, body)
    assert answer.text == DECLINE_TEXT
    assert [d.reason for d in answer.citation_check.dropped] == ["no_citation"]


def test_a_reply_without_a_status_line_is_enforced_as_an_answer_and_flagged():
    fixture = load("answered_q0072")
    text = fixture["response"]["content"][-1]["text"].removeprefix("STATUS: answered\n")
    answer, _ = _answer(fixture, with_text(fixture["response"], text))
    assert answer.status == "no_status"
    assert answer.text == text


def test_the_status_line_is_read_case_insensitively():
    fixture = load("declined_q0005")
    answer, _ = _answer(fixture, with_text(fixture["response"], "status: Declined\n"))
    assert answer.status == "declined"


def test_a_refusal_is_recorded_with_no_answer_text():
    fixture = load("answered_q0072")
    body = {**fixture["response"], "content": [], "stop_reason": "refusal",
            "stop_details": {"type": "refusal", "category": "general_harms",
                             "explanation": None}}
    answer, _ = _answer(fixture, body)
    assert (answer.status, answer.text, answer.refusal_category) == ("refused", "",
                                                                     "general_harms")


def test_a_truncated_reply_records_its_stop_reason():
    fixture = load("answered_q0072")
    answer, _ = _answer(fixture, {**fixture["response"], "stop_reason": "max_tokens"})
    assert answer.stop_reason == "max_tokens"


def test_a_cached_response_is_flagged():
    answer, _ = _answer(load("answered_q0072"), from_cache=True)
    assert answer.from_cache is True
