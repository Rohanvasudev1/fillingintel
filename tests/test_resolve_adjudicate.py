"""The claude-sonnet-5-5 adjudicator: its request and the checks on its reply (Step 9).

Nodes carry real evidence from NVDA run 1 and chunk text from the NVDA FY2026
10-K fixture. Each hand-written reply breaks one rule, and goes through
resolve_nodes() so the test sees what a run sees: a merge or a counted reason.
Real replies, replayed offline, are in test_resolve_adjudicate_recorded.py.
"""
import json

import pytest

from resolve.adjudicate import ClaudeAdjudicator
from resolve.config import ResolveConfig
from resolve.decisions import Answer, Failure, RelatedKind
from resolve.log import Method, Stamp
from resolve.nodes import EvidenceRef, ResolveNode
from resolve.pipeline import resolve_nodes
from resolve.prompt import PromptError, load_prompt
from resolve.request import ADJUDICATOR_EFFORT, ADJUDICATOR_MODEL, MAX_CHUNKS_PER_NODE
from retrieve.answer_model import AnswerModelError, ApiResponse
from tests.extract_fakes import body
from tests.resolve_helpers import NVDA_10K

STAMP = Stamp(config_hash="c" * 64, commit="abc1234", timestamp="2026-10-10T12:00:00+00:00")
EVERY_PAIR = ResolveConfig(fuzzy_cutoff=0.0, top_k=0)
CHUNK_49, CHUNK_50 = f"{NVDA_10K}:0049", f"{NVDA_10K}:0050"

GDPR = ResolveNode("Regulation:gdpr", "Regulation", "GDPR", (EvidenceRef(
    CHUNK_50, "the GDPR governs the transfer of personal data to countries outside of the EEA"),))
LONG = ResolveNode("Regulation:general data protection regulation", "Regulation",
                   "General Data Protection Regulation", (EvidenceRef(
                       CHUNK_49, "The European Union adopted the General Data Protection "
                                 "Regulation, or GDPR"),))
UK = ResolveNode("Regulation:u.k. gdpr", "Regulation", "U.K. GDPR", (EvidenceRef(
    CHUNK_49, "the United Kingdom similarly adopted the U\\.K\\. GDPR"),))

QUOTE_GDPR = "the GDPR governs the transfer of personal data"
QUOTE_LONG = "adopted the General Data Protection Regulation, or GDPR"


def reply(answer="same", related_kind=None, quote_a=QUOTE_GDPR, chunk_id_a=CHUNK_50,
          quote_b=QUOTE_LONG, chunk_id_b=CHUNK_49, reason="The filing defines GDPR."):
    return {"answer": answer, "related_kind": related_kind, "quote_a": quote_a,
            "chunk_id_a": chunk_id_a, "quote_b": quote_b, "chunk_id_b": chunk_id_b,
            "reason": reason}


class FakeModel:
    """Answers every request with *answer* (a reply dict, raw text, a body or an exception)."""

    def __init__(self, answer):
        self._answer = answer
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if isinstance(self._answer, Exception):
            raise self._answer
        if isinstance(self._answer, dict) and "content" in self._answer:
            return ApiResponse(body=self._answer, api_ms=1.0)
        return ApiResponse(body=body(self._answer), api_ms=1.0)


@pytest.fixture(scope="module")
def texts(test_graph_chunks):
    return {chunk_id: text for chunk_id, (_, text) in test_graph_chunks.items()}


def _run(answer, texts, nodes=(GDPR, LONG)):
    model = FakeModel(answer)
    adjudicator = ClaudeAdjudicator(model, load_prompt(), texts)
    result = resolve_nodes(nodes, None, adjudicator, EVERY_PAIR, stamp=STAMP)
    return result, model


def test_same_with_both_quotes_found_merges(texts):
    result, _ = _run(reply(), texts)
    (decision,) = result.decisions
    assert decision.answer is Answer.SAME
    assert decision.quotes_verified and decision.failure is None
    (merge,) = result.merges
    assert merge.method is Method.LLM
    assert set(merge.members) == {GDPR.key, LONG.key}
    assert {q.quote for q in merge.evidence} == {QUOTE_GDPR, QUOTE_LONG}


def test_every_decision_records_the_prompt_version_and_usage(texts):
    result, _ = _run(reply(), texts)
    (decision,) = result.decisions
    assert decision.prompt_version == load_prompt().prompt_version
    assert decision.prompt_version.startswith("resolve/v1@")
    assert decision.usage is not None and decision.usage.output_tokens > 0
    assert decision.decision_id.startswith("d-")


def test_a_quote_found_in_no_chunk_does_not_merge(texts):
    result, _ = _run(reply(quote_b="the General Data Protection Act"), texts)
    (decision,) = result.decisions
    assert decision.failure == Failure.QUOTE_NOT_FOUND
    assert not decision.quotes_verified
    assert result.merges == ()


def test_a_quote_from_the_other_nodes_chunk_does_not_merge(texts):
    for chunk_id_a in (CHUNK_49, CHUNK_50):  # named as the other's chunk, or as its own
        result, _ = _run(reply(quote_a=QUOTE_LONG, chunk_id_a=chunk_id_a), texts)
        assert result.decisions[0].failure == Failure.QUOTE_FROM_OTHER_NODE
        assert result.merges == ()


def test_a_quote_in_a_chunk_both_nodes_share_is_each_nodes_own(texts):
    result, _ = _run(reply(answer="related", related_kind="variant", quote_a=QUOTE_LONG,
                           chunk_id_a=CHUNK_49, quote_b="the U.K. GDPR", chunk_id_b=CHUNK_49),
                     texts, nodes=(LONG, UK))
    (decision,) = result.decisions
    assert decision.quotes_verified  # markdown escapes dropped, as in Step 8's span check
    assert decision.failure == "related:variant"
    assert [d.related_kind for d in result.related] == [RelatedKind.VARIANT]


def test_unsure_is_counted_and_does_not_merge(texts):
    result, _ = _run(reply(answer="unsure"), texts)
    assert result.counts.failures == {"unsure": 1}
    assert result.counts.answers == {"unsure": 1}
    assert result.merges == ()


def test_distinct_is_counted_by_its_answer(texts):
    result, _ = _run(reply(answer="distinct"), texts)
    assert result.counts.failures == {"distinct": 1}


def test_related_without_a_kind_is_an_invalid_reply(texts):
    result, _ = _run(reply(answer="related"), texts)
    (decision,) = result.decisions
    assert decision.answer is None
    assert decision.failure == Failure.INVALID_REPLY
    assert result.related == ()


def test_a_kind_on_another_answer_is_an_invalid_reply(texts):
    result, _ = _run(reply(related_kind="variant"), texts)
    assert result.decisions[0].failure == Failure.INVALID_REPLY
    assert result.merges == ()


@pytest.mark.parametrize("broken", [
    "not json {",
    json.dumps({**reply(), "answer": "maybe"}),
    json.dumps({k: v for k, v in reply().items() if k != "reason"}),
    json.dumps([reply()]),
])
def test_a_reply_not_in_the_schemas_shape_is_invalid(texts, broken):
    result, _ = _run(broken, texts)
    assert result.counts.failures == {"invalid_reply": 1}
    assert result.counts.answers == {"none": 1}


def test_a_cut_off_reply_is_invalid(texts):
    result, _ = _run(body(reply(), stop_reason="max_tokens"), texts)
    assert result.decisions[0].failure == Failure.INVALID_REPLY


def test_a_failed_call_is_counted_and_the_other_pairs_still_run(texts):
    result, model = _run(AnswerModelError("HTTP 529"), texts, nodes=(GDPR, LONG, UK))
    assert len(model.requests) == 3
    assert result.counts.failures == {"call_failed": 3}
    assert all(d.usage is None for d in result.decisions)


def test_the_request_carries_names_label_spans_and_chunk_text(texts):
    _, model = _run(reply(), texts)
    (request,) = model.requests
    params = request.params()
    assert params["model"] == ADJUDICATOR_MODEL == "claude-sonnet-5-5"
    assert params["output_config"]["effort"] == ADJUDICATOR_EFFORT == "high"
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert not {"temperature", "top_p", "top_k", "tools"} & set(params)
    user = params["messages"][0]["content"]
    assert "Regulation" in user
    assert "GDPR" in user and "General Data Protection Regulation" in user
    for node in (GDPR, LONG):
        assert node.evidence[0].span in user
    assert texts[CHUNK_49] in user and texts[CHUNK_50] in user
    assert "same entity" in params["system"][0]["text"].lower()


def test_a_shared_chunk_is_sent_once(texts):
    _, model = _run(reply(), texts, nodes=(LONG, UK))
    user = model.requests[0].params()["messages"][0]["content"]
    assert user.count(texts[CHUNK_49]) == 1


def test_only_the_first_chunks_of_a_node_are_sent_and_checked(texts):
    ordinals = range(40, 41 + MAX_CHUNKS_PER_NODE)
    many = ResolveNode("Regulation:gdpr", "Regulation", "GDPR", tuple(
        EvidenceRef(f"{NVDA_10K}:{o:04d}", "x") for o in ordinals))
    last = f"{NVDA_10K}:{ordinals[-1]:04d}"
    quote = texts[last][:60]
    result, model = _run(reply(quote_a=quote, chunk_id_a=last), texts, nodes=(many, LONG))
    assert texts[last] not in model.requests[0].params()["messages"][0]["content"]
    assert result.decisions[0].failure == Failure.QUOTE_NOT_FOUND


def test_the_same_request_twice_has_one_cache_key(texts):
    _, first = _run(reply(), texts)
    _, second = _run(reply(), texts)
    assert first.requests[0].cache_key() == second.requests[0].cache_key()


def test_a_missing_chunk_text_is_an_error_not_a_decision(texts):
    with pytest.raises(ValueError, match="no text"):
        _run(reply(), {CHUNK_50: texts[CHUNK_50]})


def test_a_changed_prompt_file_is_refused(tmp_path):
    from resolve.prompt import PROMPTS_DIR
    changed = tmp_path / "v1.md"
    changed.write_text((PROMPTS_DIR / "v1.md").read_text(encoding="utf-8") + "\nmore\n",
                       encoding="utf-8")
    with pytest.raises(PromptError, match="changed"):
        load_prompt("v1", tmp_path)
    with pytest.raises(PromptError, match="not published"):
        load_prompt("v9")
