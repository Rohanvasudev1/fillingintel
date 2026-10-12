"""The adjudicator on real claude-haiku-5-5 replies to NVDA run 1 pairs, offline (Step 9).

Ten pairs recorded once by scripts/capture_resolve_responses.py with prompt
resolve/v2 (ticket 06), each fixture holding both nodes as run 1 extracted them. The chunk
text comes from the NVDA FY2026 10-K fixture. These tests pin what the model
really answered and that the code turns each answer into the right merge or
counted reason; they do not claim the answers are right (ticket 04 scores that).
"""
import json

import pytest

from resolve.adjudicate import ClaudeAdjudicator
from resolve.config import ResolveConfig
from resolve.decisions import Answer, RelatedKind
from resolve.log import Method, Stamp
from resolve.nodes import EvidenceRef, MetricFacts, ResolveNode
from resolve.pipeline import resolve_nodes
from resolve.prompt import load_prompt
from retrieve.answer_model import AnswerModelError, ApiResponse
from tests.anthropic_fixtures import FIXTURES

STAMP = Stamp(config_hash="c" * 64, commit="abc1234", timestamp="2026-10-10T12:00:00+00:00")
EVERY_PAIR = ResolveConfig(fuzzy_cutoff=0.0, top_k=0)
RECORDED_UNDER = "resolve/v2@5b5e4a7d"
SAME = ("groq_license_agreements", "huawei", "gdpr_long_name", "obbba", "revenue")
RELATED = {"gdpr_uk": RelatedKind.VARIANT}
# Haiku is more cautious than Sonnet was under v1, which answered same for the Groq IP
# arrangement and Teter, and related for Blackwell / GB200 and GeForce NOW.
NOT_MERGED = {"groq_ip_arrangement": "unsure", "teter": "unsure", "blackwell_gb200": "unsure",
              "geforce_now": "distinct"}


def _node(data):
    metric = data["metric"]
    return ResolveNode(data["key"], data["label"], data["name"],
                       tuple(EvidenceRef(**e) for e in data["evidence"]),
                       None if metric is None else MetricFacts(**metric))


def _load(name):
    return json.loads((FIXTURES / f"resolve_{name}.json").read_text(encoding="utf-8"))


class Replay:
    """Serves each recorded body for its pair; any other pair fails as an API error would."""

    def __init__(self, *names):
        self._bodies = {}
        for name in names:
            recorded = _load(name)
            pair = (recorded["node_a"]["key"], recorded["node_b"]["key"])
            self._bodies[pair] = recorded["response"]

    def complete(self, request):
        body = self._bodies.get((request.key_a, request.key_b))
        if body is None:
            raise AnswerModelError("no recorded reply for this pair")
        return ApiResponse(body=body, api_ms=1.0, from_cache=True)


@pytest.fixture(scope="module")
def texts(test_graph_chunks):
    return {chunk_id: text for chunk_id, (_, text) in test_graph_chunks.items()}


def _resolve(texts, *names):
    nodes = {}
    for name in names:
        recorded = _load(name)
        for side in ("node_a", "node_b"):
            nodes[recorded[side]["key"]] = _node(recorded[side])
    adjudicator = ClaudeAdjudicator(Replay(*names), load_prompt(), texts)
    return resolve_nodes(tuple(nodes.values()), None, adjudicator, EVERY_PAIR, stamp=STAMP)


def test_the_replies_were_recorded_with_the_current_prompt():
    assert load_prompt().prompt_version == RECORDED_UNDER
    for name in (*SAME, *RELATED, *NOT_MERGED):
        assert _load(name)["prompt_version"] == RECORDED_UNDER


@pytest.mark.parametrize("name", SAME)
def test_same_with_verified_quotes_merges(texts, name):
    result = _resolve(texts, name)
    (decision,) = result.decisions
    assert decision.answer is Answer.SAME
    assert decision.quotes_verified
    (merge,) = result.merges
    assert merge.method is Method.LLM
    assert merge.prompt_version == RECORDED_UNDER


@pytest.mark.parametrize("name", RELATED)
def test_related_pairs_are_logged_by_kind_and_never_merge(texts, name):
    result = _resolve(texts, name)
    (decision,) = result.decisions
    assert decision.answer is Answer.RELATED
    assert decision.related_kind is RELATED[name]
    assert result.related == (decision,)
    assert result.merges == ()
    assert result.counts.failures == {f"related:{RELATED[name].value}": 1}


@pytest.mark.parametrize("name", NOT_MERGED)
def test_unsure_and_distinct_answers_are_counted_and_never_merge(texts, name):
    result = _resolve(texts, name)
    (decision,) = result.decisions
    assert decision.answer.value == NOT_MERGED[name]
    assert result.merges == ()
    assert result.counts.failures == {NOT_MERGED[name]: 1}


def test_an_unsure_groq_pair_leaves_the_other_groq_merge_alone(texts):
    result = _resolve(texts, "groq_license_agreements", "groq_ip_arrangement")
    assert result.counts.failures == {"call_failed": 1, "unsure": 1}  # one pair unrecorded
    (merge,) = result.merges
    assert len(merge.members) == 2
    assert result.held_back == ()


def test_the_uk_regime_stays_outside_the_gdpr_group(texts):
    gdpr_only = _resolve(texts, "gdpr_long_name")
    assert len(gdpr_only.merges) == 1
    joint = _resolve(texts, "gdpr_long_name", "gdpr_uk")
    # GDPR = General Data Protection Regulation, GDPR ~ U.K. GDPR (related): U.K. GDPR is
    # outside the `same` group, so the group stays clean and merges.
    assert len(joint.merges) == 1
    assert "Regulation:u.k. gdpr" not in joint.merges[0].members
    assert len(joint.related) == 1
