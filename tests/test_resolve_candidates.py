"""Cheap scores and candidate rules over same-label pairs (Step 9).

Each rule is shown firing alone: the other rules are either switched off in the
config (top_k 0 turns the embedding rule off) or cannot fire on the names used.
"""
import math

import pytest

from resolve.candidates import Rule, score_pairs
from resolve.config import ResolveConfig
from tests.resolve_helpers import AMD_10K, metric, node

NO_EMBEDDING = ResolveConfig(top_k=0)


def _unit(*xs: float) -> tuple[float, ...]:
    norm = math.sqrt(sum(x * x for x in xs))
    return tuple(x / norm for x in xs)


def _pair(pairs, a, b):
    wanted = {a.key, b.key}
    found = [p for p in pairs if {p.key_a, p.key_b} == wanted]
    assert len(found) == 1, f"expected one pair for {wanted}, found {len(found)}"
    return found[0]


def test_every_same_label_pair_is_scored_once_and_labels_never_mix():
    nodes = (node("Organization", "Groq"), node("Organization", "Broadcom"),
             node("Organization", "Fabrinet"), node("Product", "Groq"))
    pairs = score_pairs(nodes, {}, NO_EMBEDDING)
    assert {(p.label, p.key_a, p.key_b) for p in pairs} == {
        ("Organization", "Organization:broadcom", "Organization:groq"),
        ("Organization", "Organization:broadcom", "Organization:fabrinet"),
        ("Organization", "Organization:fabrinet", "Organization:groq"),
    }
    assert all(p.key_a < p.key_b for p in pairs)


def test_a_pair_no_rule_fires_on_is_scored_but_not_a_candidate():
    a, b = node("Organization", "Broadcom"), node("Organization", "Fabrinet")
    pair = _pair(score_pairs((a, b), {}, NO_EMBEDDING), a, b)
    assert pair.rules == frozenset()
    assert not pair.is_candidate
    assert 0 <= pair.fuzzy < 90


def test_normalized_equal_fires_on_names_that_differ_by_suffix_and_punctuation():
    a, b = node("Organization", "Groq, Inc."), node("Organization", "Groq")
    pair = _pair(score_pairs((a, b), {}, ResolveConfig(top_k=0, fuzzy_cutoff=101)), a, b)
    assert Rule.NORMALIZED_EQUAL in pair.rules


def test_fuzzy_fires_alone_at_the_cutoff():
    a = node("Organization", "Hon Hai Precision Industry Co., Ltd.")
    b = node("Organization", "Hon Hai Precision Industries")
    config = ResolveConfig(top_k=0, rare_word_max_keys=0)
    pair = _pair(score_pairs((a, b), {}, config), a, b)
    assert pair.rules == frozenset({Rule.FUZZY})
    assert pair.fuzzy >= 90


def test_fuzzy_does_not_fire_below_the_cutoff():
    a = node("Organization", "Hon Hai Precision Industry Co., Ltd.")
    b = node("Organization", "Hon Hai Precision Industries")
    pair = _pair(score_pairs((a, b), {}, ResolveConfig(top_k=0, rare_word_max_keys=0,
                                                       fuzzy_cutoff=99)), a, b)
    assert Rule.FUZZY not in pair.rules


@pytest.mark.parametrize(("short", "long"), [
    ("GDPR", "General Data Protection Regulation"),
    ("OBBBA", "One Big Beautiful Bill Act"),
    ("SEC", "Securities and Exchange Commission"),
    ("DOJ", "Department of Justice"),
])
def test_acronym_fires_alone(short, long):
    a, b = node("Regulation", short), node("Regulation", long)
    pair = _pair(score_pairs((a, b), {}, NO_EMBEDDING), a, b)
    assert pair.rules == frozenset({Rule.ACRONYM})
    assert pair.acronym


def test_acronym_needs_a_one_word_name_and_a_longer_one():
    a, b = node("Regulation", "GD PR"), node("Regulation", "General Data Protection Regulation")
    pair = _pair(score_pairs((a, b), {}, NO_EMBEDDING), a, b)
    assert Rule.ACRONYM not in pair.rules


def test_a_shared_rare_word_fires_alone():
    a = node("Organization", "Lumentum Holdings Inc.")
    b = node("Organization", "Lumentum Operations")
    config = ResolveConfig(top_k=0, fuzzy_cutoff=101, rare_word_max_keys=3)
    pair = _pair(score_pairs((a, b), {}, config), a, b)
    assert pair.rules == frozenset({Rule.RARE_WORD})
    assert pair.shared_rare_words == ("lumentum",)


def test_a_word_in_too_many_names_is_not_rare():
    nodes = tuple(node("Organization", f"{w} Systems") for w in ("Cisco", "Arista", "Wistron"))
    config = ResolveConfig(top_k=0, fuzzy_cutoff=101, rare_word_max_keys=2)
    assert not any(p.is_candidate for p in score_pairs(nodes, {}, config))


def test_top_k_embedding_neighbours_fire_alone():
    hon_hai = node("Organization", "Hon Hai Precision Industry Co., Ltd.")
    foxconn = node("Organization", "Foxconn")
    tesla = node("Organization", "Tesla, Inc.")
    vectors = {hon_hai.key: _unit(1, 0.1, 0), foxconn.key: _unit(1, 0, 0),
               tesla.key: _unit(0, 0, 1)}
    pairs = score_pairs((hon_hai, foxconn, tesla), vectors, ResolveConfig(top_k=1))
    near = _pair(pairs, hon_hai, foxconn)
    assert near.rules == frozenset({Rule.EMBEDDING})
    assert near.embedding_rank == 1
    assert near.cosine == pytest.approx(_unit(1, 0.1, 0)[0])
    far = _pair(pairs, hon_hai, tesla)
    assert far.rules == frozenset()
    assert far.embedding_rank == 2


def test_embedding_needs_a_vector_for_every_named_node():
    a, b = node("Organization", "Groq"), node("Organization", "Tesla, Inc.")
    with pytest.raises(ValueError, match="no vector"):
        score_pairs((a, b), {a.key: _unit(1, 0)}, ResolveConfig(top_k=1))


def test_risk_factors_are_never_paired():
    nodes = (node("RiskFactor", "Export controls", key=f"{AMD_10K}:export controls"),
             node("RiskFactor", "Export controls", key="0001045810-26-000021:export controls"))
    assert score_pairs(nodes, {}, NO_EMBEDDING) == ()


def test_metric_values_pair_within_one_filing_on_equal_facts():
    revenue, total = metric("Revenue"), metric("Total revenue")
    pair = _pair(score_pairs((revenue, total), {}, NO_EMBEDDING), revenue, total)
    assert pair.rules == frozenset({Rule.SAME_FACTS})


@pytest.mark.parametrize("change", [
    {"value": 130.5}, {"unit": "USD millions"}, {"period": "FY2025"},
    {"subject": "Segment:compute & networking"}, {"accession_no": "0001045810-25-000023"},
])
def test_metric_values_with_any_fact_different_are_not_paired(change):
    revenue, other = metric("Revenue"), metric("Total revenue", **change)
    assert score_pairs((revenue, other), {}, NO_EMBEDDING) == ()


@pytest.mark.parametrize("label", ["Segment", "MarketPlatform"])
def test_per_filer_labels_pair_only_within_one_filer(label):
    nvda = node(label, "Gaming", 20, key=f"{label}:1045810:gaming")
    nvda_too = node(label, "Gaming platform", 21, key=f"{label}:1045810:gaming platform")
    amd = node(label, "Gaming", 30, key=f"{label}:2488:gaming", accession_no=AMD_10K)
    pairs = score_pairs((nvda, nvda_too, amd), {}, NO_EMBEDDING)
    assert [(p.key_a, p.key_b) for p in pairs] == [(nvda.key, nvda_too.key)]
