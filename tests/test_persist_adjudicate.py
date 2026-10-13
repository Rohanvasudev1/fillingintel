"""The persistence adjudicator: its request, reply parsing and quote checks (Step 10).

The model is a fake that returns a fixed reply; chunk text comes from the AMD
FY2025 10-K and FY2026-Q2 10-Q fixtures, so every quote check runs on real
filing text.
"""
import pytest

from persist.adjudicate import ClaudePersistAdjudicator
from persist.candidates import CandidatePair
from persist.decisions import Answer, Failure
from persist.prompt import PromptError, load_prompt
from persist.request import MAX_CHUNKS_PER_FACTOR, build_request
from retrieve.answer_model import AnswerModelError, ApiResponse
from tests.extract_fakes import body
from tests.persist_helpers import (
    CLIMATE,
    GUARANTEES_10K,
    GUARANTEES_10Q,
    LITIGATION_10Q,
    RECORDED_PAIRS,
    SEASONAL,
    earlier,
    later,
)

OLD = earlier(GUARANTEES_10K, 53)
NEW = later(GUARANTEES_10Q, 78)
CANDIDATE = CandidatePair(NEW.key, OLD.key, 1, 0.9)


@pytest.fixture(scope="module")
def texts(amd_10k_filing, amd_10q_filing):
    from ingest.chunker import chunk_filing

    return {c.chunk_id: filing.text[c.char_start:c.char_end]
            for filing in (amd_10k_filing, amd_10q_filing) for c in chunk_filing(filing)}


class FakeModel:
    def __init__(self, reply=None, error=None, stop_reason="end_turn"):
        self._reply = reply
        self._error = error
        self._stop = stop_reason
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return ApiResponse(body=body(self._reply, self._stop), api_ms=1.0)


def _reply(answer="same", quote_earlier=GUARANTEES_10K, chunk_earlier=OLD.evidence[0].chunk_id,
           quote_later=GUARANTEES_10Q, chunk_later=NEW.evidence[0].chunk_id):
    return {"answer": answer, "quote_earlier": quote_earlier, "chunk_id_earlier": chunk_earlier,
            "quote_later": quote_later, "chunk_id_later": chunk_later, "reason": "because"}


def _judge(texts, reply=None, **kwargs):
    model = FakeModel(reply if reply is not None else _reply(), **kwargs)
    return ClaudePersistAdjudicator(model, load_prompt(), texts).judge(CANDIDATE, NEW, OLD)


def test_the_helper_titles_are_real_spans_of_the_fixture_chunks(texts):
    from extract.spans import find_span

    recorded = [f for pair in RECORDED_PAIRS.values() for f in pair]
    for factor in (earlier(SEASONAL, 21, 27), OLD, earlier(CLIMATE, 41),
                   later(SEASONAL + ".", 49), NEW, later(LITIGATION_10Q, 71), *recorded):
        for e in factor.evidence:
            assert find_span(e.span, texts[e.chunk_id]) is not None, e


def test_a_same_with_both_quotes_found_links_with_the_chunks_own_text(texts):
    decision = _judge(texts)
    assert decision.answer is Answer.SAME and decision.links
    assert decision.span_earlier in texts[decision.chunk_id_earlier]
    assert decision.span_later in texts[decision.chunk_id_later]
    assert (decision.later_key, decision.earlier_key, decision.rank) == (NEW.key, OLD.key, 1)
    assert decision.prompt_version == load_prompt().prompt_version


@pytest.mark.parametrize("answer", ["distinct", "unsure", "DISTINCT"])
def test_distinct_and_unsure_do_not_link_and_are_counted_by_answer(texts, answer):
    decision = _judge(texts, _reply(answer=answer))
    assert not decision.links
    assert decision.failure == answer.casefold()


def test_a_quote_not_in_its_chunk_does_not_link(texts):
    decision = _judge(texts, _reply(quote_later="words the filing never prints"))
    assert decision.failure == Failure.QUOTE_NOT_FOUND and not decision.links
    assert decision.span_later is None


def test_a_quote_from_the_other_factors_chunk_is_named(texts):
    decision = _judge(texts, _reply(quote_later=GUARANTEES_10K,
                                    chunk_later=OLD.evidence[0].chunk_id))
    assert decision.failure == Failure.QUOTE_FROM_OTHER_FACTOR


def test_a_quote_naming_a_chunk_not_sent_does_not_link(texts):
    decision = _judge(texts, _reply(chunk_earlier="0000002488-26-000018:0021"))
    assert decision.failure == Failure.QUOTE_NOT_FOUND


@pytest.mark.parametrize("reply", [
    "not json", {"answer": "same"}, _reply(answer="related"), {**_reply(), "reason": 3},
])
def test_a_reply_not_in_the_schemas_shape_is_invalid(texts, reply):
    decision = _judge(texts, reply)
    assert decision.failure == Failure.INVALID_REPLY and decision.answer is None


def test_a_reply_cut_off_is_invalid(texts):
    assert _judge(texts, stop_reason="max_tokens").failure == Failure.INVALID_REPLY


def test_a_failed_call_is_counted_and_carries_no_usage(texts):
    decision = _judge(texts, error=AnswerModelError("overloaded"))
    assert decision.failure == Failure.CALL_FAILED and decision.usage is None


def test_the_decision_id_is_stable_and_names_the_pair(texts):
    first, second = _judge(texts), _judge(texts, _reply(answer="distinct"))
    assert first.decision_id == second.decision_id
    other = ClaudePersistAdjudicator(FakeModel(_reply()), load_prompt(), texts).judge(
        CandidatePair(NEW.key, "other", 1, 0.9), NEW, OLD)
    assert other.decision_id != first.decision_id


def test_the_request_sends_each_factors_title_spans_and_chunks_once(texts):
    shared = earlier(SEASONAL, 21, 27)
    request = build_request(load_prompt(), later(SEASONAL + ".", 49), shared, texts)
    assert request.user.count('<passage id="0000002488-26-000018:0021">') == 1
    assert request.user.count('<passage id="0000002488-26-000123:0049">') == 1
    assert request.user.index("0000002488-26-000018:0021") < request.user.index(
        "0000002488-26-000123:0049")
    assert f"title: {SEASONAL}" in request.user
    params = request.params()
    assert params["model"] == "claude-haiku-5-5"
    assert params["output_config"]["effort"] == "high"
    assert "temperature" not in params


def test_only_the_first_chunks_of_a_factor_are_sent(texts):
    many = earlier(SEASONAL, *range(20, 20 + MAX_CHUNKS_PER_FACTOR + 1))
    request = build_request(load_prompt(), NEW, many, texts)
    assert request.user.count("<passage id=") == MAX_CHUNKS_PER_FACTOR + 1


def test_a_missing_chunk_text_is_refused(texts):
    with pytest.raises(ValueError, match="no text for chunk"):
        build_request(load_prompt(), NEW, OLD, {})


def test_a_changed_prompt_file_is_refused(tmp_path):
    (tmp_path / "v1.md").write_text("# System\nchanged\n# User\n$earlier $later $passages\n",
                                    encoding="utf-8")
    with pytest.raises(PromptError, match="changed"):
        load_prompt("v1", tmp_path)


def test_the_prompt_version_names_its_hash():
    prompt = load_prompt()
    assert prompt.prompt_version == f"persist/v1@{prompt.sha256[:8]}"
    assert "same-risk rule" in prompt.system
