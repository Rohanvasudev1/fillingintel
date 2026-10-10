"""resolve_nodes(): deterministic merges and pending candidate pairs, no LLM (Step 9).

Nodes are hand-built around names and chunk IDs from NVDA run 1. The embedder
is a fake that hands back fixed vectors and counts its calls; the pipeline
test over recorded Voyage vectors is in test_resolve_recorded.py.
"""
import math

import pytest

from resolve.candidates import Rule
from resolve.config import ResolveConfig
from resolve.log import Method, Stamp
from resolve.pipeline import Resolution, resolve_nodes
from tests.resolve_helpers import AMD_10K, metric, node

STAMP = Stamp(config_hash="c" * 64, commit="abc1234", timestamp="2026-10-10T12:00:00+00:00")
NO_EMBEDDING = ResolveConfig(top_k=0)


class FakeEmbedder:
    """Gives each text a vector of its own, pointing along one axis, and counts calls."""

    def __init__(self, near: dict[str, str] | None = None):
        self.texts: list[str] = []
        self._near = near or {}

    def embed(self, texts):
        self.texts.extend(texts)
        axes = {t: i for i, t in enumerate(sorted(set(texts) - set(self._near)))}
        size = len(axes) + 1
        vectors = []
        for t in texts:
            v = [0.0] * size
            if t in self._near:
                v[axes[self._near[t]]], v[-1] = 1.0, 0.2
            else:
                v[axes[t]] = 1.0
            norm = math.sqrt(sum(x * x for x in v))
            vectors.append(tuple(x / norm for x in v))
        return tuple(vectors)


def _resolve(nodes, config=NO_EMBEDDING, embedder=None, log=()) -> Resolution:
    return resolve_nodes(nodes, embedder, None, config, log=log, stamp=STAMP)


GROQ = (node("Organization", "Groq", 30), node("Organization", "Groq, Inc", 31),
        node("Organization", "Groq, Inc.", 32, 33))


def test_names_equal_after_normalization_merge_deterministically():
    (merge,) = _resolve(GROQ).merges
    assert merge.method is Method.DETERMINISTIC
    assert merge.rule == Rule.NORMALIZED_EQUAL
    assert dict(merge.members) == {"Organization:groq": 1, "Organization:groq, inc": 1,
                                   "Organization:groq, inc.": 2}
    assert merge.canonical == "Organization:groq, inc."
    assert {q.chunk_id for q in merge.evidence} == {
        "0001045810-26-000021:0030", "0001045810-26-000021:0031", "0001045810-26-000021:0032"}
    assert merge.stamp == STAMP


def test_the_canonical_key_ties_to_the_smallest_key():
    (merge,) = _resolve(GROQ[:2]).merges
    assert merge.canonical == "Organization:groq"


def test_a_filer_named_as_an_organization_merges_into_its_company():
    nvidia = node("Organization", "NVIDIA Corporation", 40, accession_no=AMD_10K)
    (merge,) = _resolve((nvidia, node("Organization", "Broadcom", 41))).merges
    assert merge.rule == "filer_table"
    assert dict(merge.members) == {"Company:1045810": 0, nvidia.key: 1}
    assert merge.canonical == "Company:1045810"
    table = [q for q in merge.evidence if q.chunk_id is None]
    assert [(q.key, q.name) for q in table] == [("Company:1045810", "NVIDIA Corporation")]


def test_a_filer_name_on_another_label_does_not_map_to_the_company():
    assert _resolve((node("Product", "NVIDIA", 4), node("Product", "Grace", 5))).merges == ()


def test_candidate_pairs_that_are_not_deterministic_stay_pending_without_an_adjudicator():
    gdpr = node("Regulation", "GDPR", 50)
    full = node("Regulation", "General Data Protection Regulation", 51)
    result = _resolve((gdpr, full))
    assert result.merges == ()
    (pending,) = result.pending
    assert (pending.key_a, pending.key_b) == (gdpr.key, full.key)
    assert pending.rules == frozenset({Rule.ACRONYM})


def test_pairs_inside_one_deterministic_group_are_not_pending():
    assert _resolve(GROQ).pending == ()


def test_metric_values_pair_only_within_one_filing():
    revenue, total = metric("Revenue"), metric("Total revenue")
    other_filing = metric("Revenue", accession_no="0001045810-25-000023")
    result = _resolve((revenue, total, other_filing))
    assert [(p.key_a, p.key_b) for p in result.pending] == [(revenue.key, total.key)]


def test_a_rerun_with_the_log_appends_nothing():
    first = _resolve(GROQ)
    assert _resolve(GROQ, log=first.merges).merges == ()
    assert len(_resolve(GROQ, log=first.merges).proposed) == 1


def test_the_embedder_sees_label_and_name_and_finds_neighbours():
    hon_hai = node("Organization", "Hon Hai Precision Industry Co., Ltd.", 60)
    foxconn = node("Organization", "Foxconn", 61)
    tesla = node("Organization", "Tesla, Inc.", 62)
    embedder = FakeEmbedder(near={"Organization: Foxconn":
                                  "Organization: Hon Hai Precision Industry Co., Ltd."})
    result = _resolve((hon_hai, foxconn, tesla), ResolveConfig(top_k=1), embedder)
    assert sorted(embedder.texts) == ["Organization: Foxconn",
                                      "Organization: Hon Hai Precision Industry Co., Ltd.",
                                      "Organization: Tesla, Inc."]
    assert any({p.key_a, p.key_b} == {hon_hai.key, foxconn.key} and Rule.EMBEDDING in p.rules
               for p in result.pending)


def test_the_name_span_form_embeds_the_first_evidence_span():
    embedder = FakeEmbedder()
    a = node("Product", "GeForce NOW", 70)
    _resolve((a, node("Product", "Grace", 71)), ResolveConfig(top_k=1, embed_text="name_span"),
             embedder)
    assert "GeForce NOW: GeForce NOW" in embedder.texts


def test_metric_values_and_risk_factors_are_not_embedded():
    embedder = FakeEmbedder()
    _resolve((metric("Revenue"), metric("Total revenue"),
              node("RiskFactor", "Export controls", key=f"{AMD_10K}:export controls")),
             ResolveConfig(top_k=3), embedder)
    assert embedder.texts == []


def test_counts_by_label_rule_and_pending():
    gdpr = (node("Regulation", "GDPR", 50),
            node("Regulation", "General Data Protection Regulation"))
    counts = _resolve((*GROQ, *gdpr)).counts
    assert counts.nodes_by_label == {"Organization": 3, "Regulation": 2}
    assert counts.pairs_by_label == {"Organization": 3, "Regulation": 1}
    assert counts.candidates_by_label == {"Organization": 3, "Regulation": 1}
    assert counts.candidates_by_rule[Rule.NORMALIZED_EQUAL] == 3
    assert counts.candidates_by_rule[Rule.ACRONYM] == 1
    assert counts.only_rule[Rule.ACRONYM] == 1
    assert counts.merges_by_rule == {"normalized_equal": 1}
    assert counts.pending_by_label == {"Regulation": 1}


def test_an_adjudicator_is_not_wired_yet():
    with pytest.raises(NotImplementedError, match="ticket 03"):
        resolve_nodes(GROQ, None, object(), NO_EMBEDDING, stamp=STAMP)


def test_embedding_without_an_embedder_is_refused():
    with pytest.raises(ValueError, match="embedder"):
        _resolve(GROQ, ResolveConfig(top_k=3), None)


def test_two_filers_same_named_segments_never_merge():
    nvda = node("Segment", "Gaming", 20, key="Segment:1045810:gaming")
    amd = node("Segment", "Gaming", 30, key="Segment:2488:gaming", accession_no=AMD_10K)
    result = _resolve((nvda, amd))
    assert result.merges == () and result.pending == ()
