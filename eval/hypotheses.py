"""H1–H5 verdicts from docs/RESEARCH-PLAN.md's pre-registered rules (Step 13).

Each function takes the numbers its rule reads and returns a ``Verdict``: the
rule as printed in ``benchmarks/results.md``, the verdict, and one line per
number read.  "Beats by at least x" needs the mean paired difference at or
above x and an interval excluding 0 (the plan's test for a difference).  A
verdict read on fewer than 10 questions is directional, with whether the rule
was met; "not testable" when the runs lack what the rule reads.  These labels
extend the spec's "supported", "not supported" and "directional" so a reader sees
which way a directional result fell.  H6 reads the pre-registered router's dev run
(ticket 03): the pre-registered map gives each class its own arm, so the router picks
the gold class's arm exactly when its predicted class is the gold class.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from eval.bootstrap import is_directional
from eval.compare import PairedDifference

SUPPORTED = "supported"
NOT_SUPPORTED = "not supported"
DIRECTIONAL_MET = "directional, rule met"
DIRECTIONAL_NOT_MET = "directional, rule not met"
NOT_TESTABLE = "not testable"

H1_MARGIN = 0.05
H2_MARGIN = 0.10
H4_MARGIN = 0.10
H5_LATENCY_FACTOR = 2.0
H6_ACCURACY = 0.80
PERIOD = "same_company_other_period"

H1_RULE = ("H1, vector holds on lookups: on lookup, for every graph arm, the mean paired "
           f"difference (graph - vector) in recall@5 is at most {H1_MARGIN}")
H2_RULE = ("H2, traversal helps on joins: on multi_hop, the mean paired difference "
           f"(graph_traversal - vector) in recall@10 is at least {H2_MARGIN} and its 95% "
           "interval excludes 0")
H3_RULE = ("H3, period confusion is the main failure: for every arm, more than half of the "
           "answerable questions with wrong evidence have a same_company_other_period hard "
           "negative among their hits; an arm with no wrong evidence is listed and skipped")
H4_RULE = ("H4, global questions favour community summaries: on global, the mean paired "
           f"difference (graph_global - vector) in judged answer relevancy is at least "
           f"{H4_MARGIN} and its 95% interval excludes 0")
H5_RULE = ("H5, graphs cost more: on the questions each graph arm and vector both answered, "
           f"every graph arm's p95 total_ms is at least {H5_LATENCY_FACTOR:g}x vector's and its "
           "mean answer-side cost per question is higher")


@dataclass(frozen=True)
class Verdict:
    rule: str
    verdict: str
    readings: tuple[str, ...]


def _verdict(met: bool, directional: bool) -> str:
    if directional:
        return DIRECTIONAL_MET if met else DIRECTIONAL_NOT_MET
    return SUPPORTED if met else NOT_SUPPORTED


def _reading(name: str, d: PairedDifference) -> str:
    return f"{name}: mean {d.mean:+.3f} [{d.low:+.3f}, {d.high:+.3f}], n {d.n}"


def _beats(rule: str, name: str, d: PairedDifference | None, margin: float) -> Verdict:
    if d is None:
        return Verdict(rule, NOT_TESTABLE, (f"{name}: no paired questions",))
    met = d.mean >= margin and d.low > 0
    return Verdict(rule, _verdict(met, is_directional(d.n)), (_reading(name, d),))


def h1(differences: Mapping[str, PairedDifference]) -> Verdict:
    """*differences*: each graph arm's lookup recall@5 difference, graph - vector."""
    if not differences:
        return Verdict(H1_RULE, NOT_TESTABLE, ("no graph arm has lookup questions",))
    met = all(d.mean <= H1_MARGIN for d in differences.values())
    directional = any(is_directional(d.n) for d in differences.values())
    return Verdict(H1_RULE, _verdict(met, directional),
                   tuple(_reading(f"{arm} - vector", d) for arm, d in differences.items()))


def h2(difference: PairedDifference | None) -> Verdict:
    """*difference*: multi_hop recall@10, graph_traversal - vector."""
    return _beats(H2_RULE, "graph_traversal - vector", difference, H2_MARGIN)


def h3(wrong: Mapping[str, Sequence[Sequence[str]]]) -> Verdict:
    """*wrong*: per arm, the hard-negative relations hit on each answerable question with
    wrong evidence."""
    readings, shares = [], []
    for arm, questions in wrong.items():
        if not questions:
            readings.append(f"{arm}: no wrong-evidence questions")
            continue
        period = sum(PERIOD in relations for relations in questions)
        shares.append(period / len(questions) > 0.5)
        readings.append(f"{arm}: {period} of {len(questions)} wrong-evidence questions "
                        f"involve {PERIOD}")
    if not shares:
        return Verdict(H3_RULE, NOT_TESTABLE, tuple(readings))
    # H3 pools the answerable classes, so the plan's per-class directional rule never applies.
    return Verdict(H3_RULE, _verdict(all(shares), directional=False), tuple(readings))


def h4(difference: PairedDifference | None) -> Verdict:
    """*difference*: global judged answer relevancy, graph_global - vector."""
    return _beats(H4_RULE, "graph_global - vector", difference, H4_MARGIN)


@dataclass(frozen=True)
class ArmLatencyCost:
    p95_total_ms: float | None  # None when the runs did not record total_ms
    cost: float  # mean answer-side USD per question
    n: int


def h5(vector: Mapping[str, ArmLatencyCost], arms: Mapping[str, ArmLatencyCost]) -> Verdict:
    """*arms*: each graph arm's numbers; *vector*: vector's on the same questions, by arm."""
    if not arms:
        return Verdict(H5_RULE, NOT_TESTABLE, ("no graph arm answered questions",))
    readings, met, testable = [], True, True
    for arm, own in arms.items():
        base = vector[arm]
        costlier = own.cost > base.cost
        cost = f"cost ${own.cost:.4f} against ${base.cost:.4f}"
        if own.p95_total_ms is None or base.p95_total_ms is None:
            testable = False
            readings.append(f"{arm}: total_ms not recorded in these runs; {cost}, n {own.n}")
            continue
        ratio = own.p95_total_ms / base.p95_total_ms
        met = met and ratio >= H5_LATENCY_FACTOR and costlier
        readings.append(f"{arm}: p95 total_ms {own.p95_total_ms:,.0f} against "
                        f"{base.p95_total_ms:,.0f} ({ratio:.2f}x); {cost}, n {own.n}")
    if not testable:
        return Verdict(H5_RULE, NOT_TESTABLE, tuple(readings))
    directional = any(is_directional(a.n) for a in arms.values())
    return Verdict(H5_RULE, _verdict(met, directional), tuple(readings))


H6_RULE = ("H6, the router is useful but imperfect: on dev, the pre-registered router picks the "
           f"arm matching the gold class (its predicted class is the gold class) on at least "
           f"{H6_ACCURACY:.0%} of answerable and decline questions, and declines every decline "
           "question; read on the point estimate, against agent-drafted class labels")


@dataclass(frozen=True)
class RouterAccuracy:
    correct: int  # answerable and decline questions whose predicted class is the gold class
    n: int
    declined: int  # decline questions the router declined
    declines: int


def h6(accuracy: RouterAccuracy | None) -> Verdict:
    if accuracy is None or accuracy.n == 0:
        return Verdict(H6_RULE, NOT_TESTABLE, ("no router run",))
    met = (accuracy.correct / accuracy.n >= H6_ACCURACY
           and accuracy.declined == accuracy.declines)
    readings = (f"router accuracy {accuracy.correct} of {accuracy.n} "
                f"({accuracy.correct / accuracy.n:.3f})",
                f"decline questions declined: {accuracy.declined} of {accuracy.declines}")
    return Verdict(H6_RULE, _verdict(met, is_directional(accuracy.n)), readings)
