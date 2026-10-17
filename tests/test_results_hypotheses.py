"""H1–H5 verdicts from RESEARCH-PLAN's pre-registered rules (Step 13 ticket 02), each
on constructed cases just either side of its threshold."""
from eval.compare import PairedDifference
from eval.hypotheses import (
    DIRECTIONAL_MET,
    DIRECTIONAL_NOT_MET,
    NOT_SUPPORTED,
    NOT_TESTABLE,
    SUPPORTED,
    ArmLatencyCost,
    h1,
    h2,
    h3,
    h4,
    h5,
)


def _d(mean: float, low: float, high: float, n: int = 27) -> PairedDifference:
    return PairedDifference("agent_drafted", "x", "m", n, mean, low, high, 0, None)


def test_h1_holds_when_no_graph_arm_beats_vector_by_more_than_005():
    assert h1({"graph_local": _d(0.05, -0.02, 0.12),
               "graph_traversal": _d(-0.03, -0.1, 0.0)}).verdict == SUPPORTED
    assert h1({"graph_local": _d(0.051, 0.0, 0.12)}).verdict == NOT_SUPPORTED
    assert h1({"graph_local": _d(0.0, -0.1, 0.1, n=9)}).verdict == DIRECTIONAL_MET
    assert h1({}).verdict == NOT_TESTABLE


def test_h2_needs_010_and_an_interval_above_zero():
    assert h2(_d(0.10, 0.01, 0.2)).verdict == SUPPORTED
    assert h2(_d(0.099, 0.01, 0.2)).verdict == NOT_SUPPORTED
    assert h2(_d(0.12, -0.01, 0.25)).verdict == NOT_SUPPORTED
    assert h2(_d(0.12, 0.01, 0.25, n=7)).verdict == DIRECTIONAL_MET
    assert h2(None).verdict == NOT_TESTABLE


def test_h3_needs_most_errors_of_every_arm_to_be_period_confusion():
    period, filing = "same_company_other_period", "same_company_same_filing"
    many = [[period]] * 6 + [[filing]] * 5  # 6 of 11: most
    half = [[period]] * 5 + [[filing]] * 5  # 5 of 10: not most
    assert h3({"vector": many, "graph_local": many}).verdict == SUPPORTED
    assert h3({"vector": many, "graph_local": half}).verdict == NOT_SUPPORTED
    # H3 pools the answerable classes: a few errors are still read in full.
    assert h3({"vector": [[period, filing]] * 3}).verdict == SUPPORTED
    assert h3({"vector": [[filing]] * 3}).verdict == NOT_SUPPORTED
    # An arm with no errors has nothing to be mostly anything; it is listed and skipped.
    verdict = h3({"vector": many, "graph_local": []})
    assert verdict.verdict == SUPPORTED
    assert any("graph_local" in r and "no wrong-evidence" in r for r in verdict.readings)
    assert h3({"vector": []}).verdict == NOT_TESTABLE


def test_h4_needs_relevancy_up_010_with_an_interval_above_zero():
    assert h4(_d(0.10, 0.02, 0.2, n=10)).verdict == SUPPORTED
    assert h4(_d(0.099, 0.02, 0.2, n=10)).verdict == NOT_SUPPORTED
    assert h4(_d(-0.088, -0.16, -0.027, n=5)).verdict == DIRECTIONAL_NOT_MET


def test_h5_needs_double_the_p95_and_a_higher_cost_for_every_graph_arm():
    vector = ArmLatencyCost(p95_total_ms=1000.0, cost=0.030, n=40)
    slow = ArmLatencyCost(p95_total_ms=2000.0, cost=0.031, n=40)
    assert h5({"graph_local": vector}, {"graph_local": slow}).verdict == SUPPORTED
    assert h5({"graph_local": vector}, {"graph_local": ArmLatencyCost(1999.0, 0.031, 40)}).verdict \
        == NOT_SUPPORTED
    assert h5({"graph_local": vector}, {"graph_local": ArmLatencyCost(2500.0, 0.030, 40)}).verdict \
        == NOT_SUPPORTED
    missing = h5({"graph_local": ArmLatencyCost(None, 0.030, 40)}, {"graph_local": slow})
    assert missing.verdict == NOT_TESTABLE
    assert any("total_ms" in r for r in missing.readings)
