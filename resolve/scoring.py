"""score(): the resolver's pairwise precision and recall against the labels (Step 9, ticket 04).

A labelled pair is predicted same when both keys have one canonical key in the
alias map (an absent key is its own), so a merge made through a chain counts.
Each labelled pair stands for its stratum's population: its weight is the
population over the number labelled in that stratum. Then, over labelled pairs,

    precision   = weighted (same and predicted) / weighted predicted
    recall      = weighted (same and predicted) / weighted same
    rules share = weighted (same and in the candidate stratum) / weighted same

A stratum with a population but no labels adds nothing and is listed as
unrepresented. Intervals are percentile bootstraps (eval.bootstrap's resample
count, confidence and percentile) that resample within each stratum, so every
resample keeps the strata's sizes and weights. The adjudicator's accuracy is
plain agreement on the labelled pairs it answered, since those all sit in the
candidate stratum and share one weight. An answer other than same (related,
distinct, unsure) counts as not same; a pair with no usable reply is counted
apart. Decisions are matched to labels whichever order their keys come in.

The intervals reuse eval.bootstrap's pieces but not bootstrap_interval(),
which resamples one list; a stratified resample needs its own loop.

Every figure is agent-labelled (gpt-6-luna); the report says so.
"""
from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from eval.bootstrap import (
    BOOTSTRAP_RESAMPLES,
    CONFIDENCE,
    Interval,
    interval_rng,
    percentile,
    ratio_interval,
)
from resolve.labeller import LABEL_NOTE
from resolve.sample import PairKeys, Stratum

SCORE_SEED = 20261012
Decisions = Mapping[PairKeys, bool | None]  # True when the adjudicator answered same


@dataclass(frozen=True, slots=True)
class PairView:
    """What scoring needs from a labelled pair."""

    key_a: str
    key_b: str
    stratum: Stratum
    same: bool

    @property
    def keys(self) -> PairKeys:
        return (self.key_a, self.key_b)


@dataclass(frozen=True, slots=True)
class Figure:
    value: float | None  # None when the denominator is zero
    interval: Interval | None


@dataclass(frozen=True, slots=True)
class StratumCounts:
    stratum: Stratum
    population: int
    labelled: int
    weight: float | None  # None when nothing in the stratum is labelled
    same: int
    predicted_same: int
    true_positives: int


@dataclass(frozen=True, slots=True)
class AdjudicatorAccuracy:
    judged: int  # labelled pairs the adjudicator answered
    correct: int
    unusable: int  # labelled pairs sent to it with no usable answer
    accuracy: Figure


@dataclass(frozen=True)
class ScoreReport:
    precision: Figure
    recall: Figure
    rules_share: Figure
    adjudicator: AdjudicatorAccuracy
    strata: tuple[StratumCounts, ...]
    unrepresented: tuple[Stratum, ...]
    seed: int
    note: str = LABEL_NOTE


@dataclass(frozen=True, slots=True)
class _Row:
    weight: float
    same: bool
    predicted: bool
    candidate: bool


_Ratio = Callable[[Sequence[_Row]], tuple[float, float]]


def score(labels: Sequence[PairView], alias_map: Mapping[str, str],
          strata: Mapping[Stratum, int], *, decisions: Decisions | None = None,
          seed: int = SCORE_SEED) -> ScoreReport:
    """Score *labels* (each pair once) against *alias_map*; *strata* gives population sizes."""
    _check(labels, strata)
    canonical = {k: alias_map.get(k, k) for p in labels for k in p.keys}
    by_stratum: dict[Stratum, list[_Row]] = {}
    for stratum in Stratum:
        mine = [p for p in labels if p.stratum is stratum]
        weight = strata.get(stratum, 0) / len(mine) if mine else 0.0
        by_stratum[stratum] = [
            _Row(weight, p.same, canonical[p.key_a] == canonical[p.key_b],
                 p.stratum is Stratum.CANDIDATE) for p in mine]
    figures = {name: _figure(by_stratum, ratio, interval_rng(seed, "resolve", name))
               for name, ratio in (("precision", _precision), ("recall", _recall),
                                   ("rules_share", _rules_share))}
    return ScoreReport(
        precision=figures["precision"], recall=figures["recall"],
        rules_share=figures["rules_share"],
        adjudicator=_adjudicator(labels, decisions or {}, seed),
        strata=tuple(_counts(s, strata.get(s, 0), rows) for s, rows in by_stratum.items()),
        unrepresented=tuple(s for s in Stratum if strata.get(s, 0) and not by_stratum[s]),
        seed=seed,
    )


def _check(labels: Sequence[PairView], strata: Mapping[Stratum, int]) -> None:
    seen: set[PairKeys] = set()
    for p in labels:
        if p.keys in seen:
            raise ValueError(f"{p.key_a} / {p.key_b} is labelled twice")
        seen.add(p.keys)
        if strata.get(p.stratum, 0) <= 0:
            raise ValueError(f"a pair is labelled in stratum {p.stratum.value}, "
                             "which has no population")


def _precision(rows: Sequence[_Row]) -> tuple[float, float]:
    return (sum(r.weight for r in rows if r.same and r.predicted),
            sum(r.weight for r in rows if r.predicted))


def _recall(rows: Sequence[_Row]) -> tuple[float, float]:
    return (sum(r.weight for r in rows if r.same and r.predicted),
            sum(r.weight for r in rows if r.same))


def _rules_share(rows: Sequence[_Row]) -> tuple[float, float]:
    return (sum(r.weight for r in rows if r.same and r.candidate),
            sum(r.weight for r in rows if r.same))


def _value(rows: Sequence[_Row], ratio: _Ratio) -> float | None:
    numerator, denominator = ratio(rows)
    return numerator / denominator if denominator else None


def _figure(by_stratum: Mapping[Stratum, Sequence[_Row]], ratio: _Ratio,
            rng: random.Random) -> Figure:
    rows = [r for group in by_stratum.values() for r in group]
    value = _value(rows, ratio)
    if value is None:
        return Figure(None, None)
    groups = [g for g in by_stratum.values() if g]
    draws = (_value([r for g in groups for r in rng.choices(g, k=len(g))], ratio)
             for _ in range(BOOTSTRAP_RESAMPLES))
    values = [v for v in draws if v is not None]
    if not values:  # possible only when no resample keeps a nonzero denominator
        return Figure(value, None)
    tail = (1 - CONFIDENCE) / 2
    return Figure(value, {"low": percentile(values, tail), "high": percentile(values, 1 - tail),
                          "resamples": len(values)})


def _adjudicator(labels: Sequence[PairView], decisions: Decisions,
                 seed: int) -> AdjudicatorAccuracy:
    by_pair = {frozenset(keys): said for keys, said in decisions.items()}
    answered = [(p, by_pair[frozenset(p.keys)]) for p in labels
                if frozenset(p.keys) in by_pair]
    judged = [(p, said) for p, said in answered if said is not None]
    outcomes = [(float(said is p.same), 1.0) for p, said in judged]
    correct = int(sum(n for n, _ in outcomes))
    unusable = len(answered) - len(judged)
    value = correct / len(judged) if judged else None
    interval = ratio_interval(outcomes, interval_rng(seed, "resolve", "adjudicator"))
    return AdjudicatorAccuracy(judged=len(judged), correct=correct, unusable=unusable,
                               accuracy=Figure(value, interval))


def _counts(stratum: Stratum, population: int, rows: Sequence[_Row]) -> StratumCounts:
    return StratumCounts(
        stratum=stratum, population=population, labelled=len(rows),
        weight=rows[0].weight if rows else None, same=sum(r.same for r in rows),
        predicted_same=sum(r.predicted for r in rows),
        true_positives=sum(r.same and r.predicted for r in rows))
