"""Compare a candidate judge with a reference judge on the same answers (Step 5, ticket 10).

The pass rule was fixed in the Step 5 spec amendment of 2026-10-05, before any
result was seen, and must not change after the run:

- yes/no verdicts (citation support, decline and not-found correctness): the
  candidate agrees with the reference on at least 85% of verdicts;
- Ragas faithfulness and answer relevancy: the mean absolute gap between the
  two judges is at most 0.10.

Verdicts are compared one by one (each kept sentence's citation-support verdict,
each behaviour verdict), pairing run *n* of one judge with run *n* of the other.
A score metric's gap is taken between each judge's mean over its runs for a
question, the number the results file reports, then averaged over questions.
A value only one judge produced is counted as missing, not compared.  A metric
fails when more than 15% of its items are missing (user decision, 2026-10-05,
before the run), and when nothing was compared.

This is a spot check, not calibration: every judged number stays "uncalibrated".
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from statistics import fmean
from types import MappingProxyType
from typing import Literal

from eval.judging.runner import JudgedQuestion
from eval.judging.scoring import JudgeConfig
from eval.schema import Class, EvalRecord, normalise_question

MIN_AGREEMENT = 0.85
MAX_MEAN_GAP = 0.10
MAX_MISSING_SHARE = 0.15  # of the items one judge or both scored
_GAP_TOLERANCE = 1e-9  # so a gap of exactly 0.10 is not failed by float rounding
VERDICT_METRICS = ("citation_support", "decline_correct", "not_found_correct")
GAP_METRICS = ("faithfulness", "answer_relevancy")
LABEL = "spot check, not calibration"

# 10 dev questions: most of a graded run is answerable questions, so they get 8.
QUOTA: Mapping[Class, int] = MappingProxyType({
    "lookup": 2, "local": 2, "multi_hop": 2, "global": 2, "decline": 1, "unanswerable": 1,
})
_SELECTION_SALT = "step5-judge-spot-check"  # keeps the order apart from the split hash


class SpotCheckError(ValueError):
    """The judges' results cannot be compared, or the questions cannot be chosen."""


@dataclass(frozen=True)
class MetricComparison:
    metric: str
    kind: Literal["agreement", "gap"]
    value: float | None  # verdict agreement, or mean absolute gap; None if nothing compared
    compared: int  # verdicts, or questions, with a value from both judges
    missing: int  # verdicts, or questions, with a value from one judge only
    passed: bool


def _check_pairing(candidate: Mapping[str, JudgedQuestion],
                   reference: Mapping[str, JudgedQuestion]) -> None:
    if set(candidate) != set(reference):
        only = sorted(set(candidate) ^ set(reference))
        raise SpotCheckError(f"the judges must score the same questions; not shared: {only[:10]}")
    for qid in candidate:
        mine = sorted(r.run for r in candidate[qid].runs)
        theirs = sorted(r.run for r in reference[qid].runs)
        if mine != theirs:
            raise SpotCheckError(f"{qid}: the judges have different runs ({mine} and {theirs})")


def _few_missing(compared: int, missing: int) -> bool:
    """Something was compared, and at most 15% of the items are missing."""
    return compared > 0 and Fraction(missing, compared + missing) <= Fraction(
        str(MAX_MISSING_SHARE))


def _agreement(metric: str, candidate: Mapping[str, JudgedQuestion],
               reference: Mapping[str, JudgedQuestion]) -> MetricComparison:
    agreed = compared = missing = 0
    for qid, judged in candidate.items():
        theirs = {r.run: r for r in reference[qid].runs}
        for run in judged.runs:
            a = run.verdicts.get(metric, ())
            b = theirs[run.run].verdicts.get(metric, ())
            if a and b and len(a) != len(b):
                raise SpotCheckError(f"{qid} run {run.run}: {metric} has {len(a)} and {len(b)} "
                                     "verdicts; the judges did not see the same answer")
            if not (a and b):
                missing += len(a) + len(b)
                continue
            compared += len(a)
            agreed += sum(x == y for x, y in zip(a, b, strict=True))
    value = agreed / compared if compared else None
    # Exact fractions, so exactly 85% passes whatever the float rounding.
    passed = (_few_missing(compared, missing)
              and Fraction(agreed, compared) >= Fraction(str(MIN_AGREEMENT)))
    return MetricComparison(metric, "agreement", value, compared, missing, passed)


def _mean_score(judged: JudgedQuestion, metric: str) -> float | None:
    values = [v for r in judged.runs if (v := r.scores.get(metric)) is not None]
    return fmean(values) if values else None


def _gap(metric: str, candidate: Mapping[str, JudgedQuestion],
         reference: Mapping[str, JudgedQuestion]) -> MetricComparison:
    gaps: list[float] = []
    missing = 0
    for qid, judged in candidate.items():
        a, b = _mean_score(judged, metric), _mean_score(reference[qid], metric)
        if a is not None and b is not None:
            gaps.append(abs(a - b))
        elif a is not None or b is not None:
            missing += 1
    value = fmean(gaps) if gaps else None
    passed = (_few_missing(len(gaps), missing) and value is not None
              and value <= MAX_MEAN_GAP + _GAP_TOLERANCE)
    return MetricComparison(metric, "gap", value, len(gaps), missing, passed)


def _metrics_present(judged: Iterable[JudgedQuestion]) -> set[str]:
    return {m for j in judged for r in j.runs for m in (*r.scores, *r.verdicts)}


def compare_judges(candidate: Mapping[str, JudgedQuestion],
                   reference: Mapping[str, JudgedQuestion]) -> tuple[MetricComparison, ...]:
    """Per metric, how far *candidate* is from *reference*, both keyed by question ID.

    Raises ``SpotCheckError`` unless both judged the same questions with the same runs.
    """
    _check_pairing(candidate, reference)
    present = _metrics_present([*candidate.values(), *reference.values()])
    return (
        *(_agreement(m, candidate, reference) for m in VERDICT_METRICS if m in present),
        *(_gap(m, candidate, reference) for m in GAP_METRICS if m in present),
    )


def _rank(record: EvalRecord) -> str:
    text = f"{_SELECTION_SALT}\n{normalise_question(record.question)}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def select_questions(records: Sequence[EvalRecord],
                     quota: Mapping[Class, int] = QUOTA) -> tuple[EvalRecord, ...]:
    """The spot-check questions: per class, the dev records first by a salted hash of the
    question text, in the quota's class order."""
    chosen: list[EvalRecord] = []
    for class_, wanted in quota.items():
        pool = sorted((r for r in records if r.split == "dev" and r.class_ == class_), key=_rank)
        if len(pool) < wanted:
            raise SpotCheckError(f"the dev split has {len(pool)} {class_} records; "
                                 f"the spot check needs {wanted}")
        chosen.extend(pool[:wanted])
    return tuple(chosen)


def passes(comparisons: Sequence[MetricComparison]) -> bool:
    """True when there was something to compare and every metric passed."""
    return bool(comparisons) and all(c.passed for c in comparisons)


def choose_judge(results: Sequence[tuple[JudgeConfig, Sequence[MetricComparison]]],
                 reference: JudgeConfig) -> JudgeConfig:
    """The first candidate, cheapest first, that passes every metric; else *reference*."""
    return next((config for config, comparisons in results if passes(comparisons)), reference)
