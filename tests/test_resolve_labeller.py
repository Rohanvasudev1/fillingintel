"""The labeller's request and reply handling, with hand-written replies (Step 9, ticket 04).

Nodes, spans and chunk IDs are NVDA run 1's; chunk text comes from the
gzipped NVDA FY2026 10-K fixture. Recorded gpt-6-luna replies are replayed in
test_resolve_label_recorded.py.
"""
import json

import pytest

from resolve.candidates import Rule
from resolve.labeller import (
    LABEL_EFFORT,
    LABEL_MODEL,
    LabelPromptError,
    build_label_request,
    label_pair,
    load_label_prompt,
)
from resolve.nodes import EvidenceRef, ResolveNode
from resolve.sample import DrawnPair, Stratum
from retrieve.answer_model import AnswerModelError, ApiResponse
from tests.openai_fixtures import RECORDED, incomplete, with_refusal, with_text
from tests.resolve_helpers import chunk, metric

GROQ = ResolveNode("Organization:groq", "Organization", "Groq",
                   (EvidenceRef(chunk(70), "license agreement with Groq"),))
GROQ_INC = ResolveNode("Organization:groq, inc.", "Organization", "Groq, Inc.",
                       (EvidenceRef(chunk(102), "Groq, Inc\\., or Groq"),))
PAIR = DrawnPair(GROQ.key, GROQ_INC.key, "Organization", Stratum.CANDIDATE)


@pytest.fixture(scope="module")
def chunk_text(test_graph_chunks):
    return lambda chunk_id: test_graph_chunks[chunk_id][1]


@pytest.fixture(scope="module")
def prompt():
    return load_label_prompt()


class FakeOpenAI:
    def __init__(self, body: dict):
        self._body = body
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return ApiResponse(body=self._body, api_ms=1.0)


def _reply(**fields) -> dict:
    reply = {"reason": "One company, with and without its legal suffix.",
             "quote_a": "license agreement with Groq", "chunk_id_a": chunk(70),
             "quote_b": "Groq, Inc., or Groq", "chunk_id_b": chunk(102), "same": True}
    return with_text(RECORDED, json.dumps({**reply, **fields}))


def test_the_prompt_file_is_pinned(tmp_path):
    (tmp_path / "v1.md").write_text("$label $node_a $node_b edited\n", encoding="utf-8")
    with pytest.raises(LabelPromptError, match="changed"):
        load_label_prompt(prompts_dir=tmp_path)


def test_the_request_carries_the_nodes_own_evidence(prompt, chunk_text):
    text = build_label_request(prompt, GROQ, GROQ_INC, chunk_text).prompt
    assert "Organization" in text
    assert "Name: Groq\n" in text and "Name: Groq, Inc." in text
    assert "license agreement with Groq" in text
    assert chunk_text(chunk(102)) in text


def test_the_request_carries_no_resolver_output(prompt, chunk_text):
    requests = []
    for stratum in Stratum:  # the pair's stratum comes from the rules; it must not reach the API
        fake = FakeOpenAI(_reply())
        label_pair(DrawnPair(GROQ.key, GROQ_INC.key, "Organization", stratum), "tuning", GROQ,
                   GROQ_INC, chunk_text, fake, prompt)
        requests += fake.requests
    assert len({r.cache_key() for r in requests}) == 1
    sent = json.dumps(requests[0].params())
    for field in [*(r.value for r in Rule if "_" in r.value), "token_set_ratio", "cosine",
                  "embedding_rank", "shared_rare_words", "merge_id", "decision_id",
                  "quotes_verified", "related_kind"]:
        assert field not in sent, field
    assert set(requests[0].params()) == {"model", "input", "reasoning", "max_output_tokens",
                                         "store", "text"}
    assert requests[0].params()["store"] is False
    assert (requests[0].model, requests[0].effort) == (LABEL_MODEL, LABEL_EFFORT)


def test_a_metric_value_shows_its_facts(prompt, chunk_text):
    a, b = metric("Revenue", ordinal=63), metric("Total revenue", ordinal=124)
    text = build_label_request(prompt, a, b, chunk_text).prompt
    assert "period: FY2026; value: 216; unit: USD billions" in text


def test_a_reply_becomes_a_label_with_checked_quotes(prompt, chunk_text):
    outcome = label_pair(PAIR, "tuning", GROQ, GROQ_INC, chunk_text,
                         FakeOpenAI(_reply()), prompt)
    label = outcome.label
    assert outcome.failure is None
    assert (label.same, label.stratum, label.set_name) == (True, Stratum.CANDIDATE, "tuning")
    assert label.quotes_verified
    assert label.prompt_version == prompt.prompt_version
    assert outcome.cost_usd > 0 and not outcome.from_cache


@pytest.mark.parametrize("fields", [
    {"quote_b": "Groq, Inc. is a supplier"},  # not in the chunk
    {"chunk_id_b": chunk(70), "quote_b": "license agreement with Groq"},  # node A's chunk
])
def test_a_quote_not_in_its_own_nodes_chunks_is_flagged(prompt, chunk_text, fields):
    outcome = label_pair(PAIR, "tuning", GROQ, GROQ_INC, chunk_text,
                         FakeOpenAI(_reply(**fields)), prompt)
    assert outcome.label is not None and not outcome.label.quotes_verified


@pytest.mark.parametrize("body, failure", [
    (with_refusal(RECORDED), "refused"),
    (incomplete(RECORDED), "incomplete (max_output_tokens)"),
    (with_text(RECORDED, "{not json"), "invalid_reply"),
    (with_text(RECORDED, json.dumps({"same": True})), "invalid_reply"),
])
def test_an_unusable_reply_gives_no_label(prompt, chunk_text, body, failure):
    outcome = label_pair(PAIR, "tuning", GROQ, GROQ_INC, chunk_text, FakeOpenAI(body), prompt)
    assert outcome.label is None
    assert outcome.failure.startswith(failure)


def test_a_failed_response_stops_the_run(prompt, chunk_text):
    with pytest.raises(AnswerModelError, match="failed"):
        label_pair(PAIR, "tuning", GROQ, GROQ_INC, chunk_text,
                   FakeOpenAI({**_reply(), "status": "failed"}), prompt)


def test_nodes_must_be_the_drawn_pair(prompt, chunk_text):
    with pytest.raises(ValueError, match="not the pair"):
        label_pair(PAIR, "tuning", GROQ_INC, GROQ, chunk_text, FakeOpenAI(_reply()), prompt)
