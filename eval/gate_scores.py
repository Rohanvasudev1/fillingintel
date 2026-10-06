"""The quality gate's numbers and how two sets of them compare (Step 6, ADR-0003).

For each gated class, and pooled over all gated questions: recall@5, recall@10
and the retrieval wrong-evidence rate.  The rate is ``eval.wrong_evidence`` with
an empty cited set, because the gate writes no answer: a question counts when a
hard negative ranks above at least one gold chunk.  Recall is the mean over the
class's questions, as in the results file's cells, so the numbers match
``eval.run`` exactly.

A recall below the baseline, or a wrong-evidence rate above it, is a drop.
Values are compared after rounding to ``DECIMALS`` places, far finer than one
question's worth (1/82), so float formatting cannot fail the gate.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean

from pydantic import BaseModel, ConfigDict, Field

from eval.metrics import recall_at_k
from eval.schema import EvalRecord
from eval.wrong_evidence import has_hard_negatives, wrong_evidence_hits

GATED_CLASSES = ("lookup", "local", "multi_hop", "global")
POOLED = "pooled"
RECALL_METRICS = ("recall@5", "recall@10")
WRONG_EVIDENCE = "wrong_evidence"
GATED_METRICS = (*RECALL_METRICS, WRONG_EVIDENCE)
DECIMALS = 9
SHOWN_DECIMALS = 6  # in printed output only; comparison uses DECIMALS

class ClassScores(BaseModel):
    """The gated numbers for one class, or pooled; JSON keys as in the results file."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    n: int = Field(ge=1)
    recall_at_5: float = Field(alias="recall@5", ge=0.0, le=1.0)
    recall_at_10: float = Field(alias="recall@10", ge=0.0, le=1.0)
    wrong_evidence: float | None = Field(ge=0.0, le=1.0)  # None: no question has a hard negative

    def metric(self, name: str) -> float | None:
        return self.model_dump(by_alias=True)[name]


@dataclass(frozen=True)
class Drop:
    """One gated number that got worse."""

    class_: str
    metric: str
    baseline: float
    current: float | None

    @property
    def delta(self) -> float | None:
        return None if self.current is None else self.current - self.baseline


def _class_scores(scored: Sequence[tuple[EvalRecord, Sequence[str]]]) -> ClassScores:
    recalls = {
        name: fmean(recall_at_k(retrieved, record.gold_chunk_ids, int(name.split("@")[1]))
                    for record, retrieved in scored)
        for name in RECALL_METRICS
    }
    flags = [bool(wrong_evidence_hits(record, retrieved, ()))
             for record, retrieved in scored if has_hard_negatives(record)]
    return ClassScores.model_validate({
        "n": len(scored),
        **recalls,
        WRONG_EVIDENCE: fmean(float(f) for f in flags) if flags else None,
    })


def score_questions(
    scored: Sequence[tuple[EvalRecord, Sequence[str]]],
) -> dict[str, ClassScores]:
    """Scores per gated class present, in class order, then pooled.

    Each pair is a record and its retrieved chunk IDs, best first.  Raises
    ``ValueError`` for a record outside the gated classes.
    """
    outside = sorted({r.class_ for r, _ in scored} - set(GATED_CLASSES))
    if outside:
        raise ValueError(f"the gate scores only {GATED_CLASSES}, not {outside}")
    by_class = {
        cls: _class_scores([(r, ids) for r, ids in scored if r.class_ == cls])
        for cls in GATED_CLASSES
        if any(r.class_ == cls for r, _ in scored)
    }
    return {**by_class, POOLED: _class_scores(scored)}


def is_worse(metric: str, baseline: float | None, current: float | None) -> bool:
    """Whether *current* is worse than *baseline* for *metric*, after rounding to ``DECIMALS``.

    Losing a number that was there is worse; gaining one is not.
    """
    if baseline is None or current is None:
        return baseline is not None
    old, new = round(baseline, DECIMALS), round(current, DECIMALS)
    return new > old if metric == WRONG_EVIDENCE else new < old


def compare(
    baseline: Mapping[str, ClassScores], current: Mapping[str, ClassScores]
) -> tuple[Drop, ...]:
    """Every gated number in *current* worse than in *baseline*, in class then metric order."""
    if list(baseline) != list(current):
        raise ValueError(f"cannot compare classes {list(current)} with {list(baseline)}")
    return tuple(
        Drop(cls, metric, old, current[cls].metric(metric))
        for cls, scores in baseline.items()
        for metric in GATED_METRICS
        if (old := scores.metric(metric)) is not None
        and is_worse(metric, old, current[cls].metric(metric))
    )


def question_set_sha256(records: Sequence[EvalRecord]) -> str:
    """SHA-256 of the gated records, in order, so any edit to them changes it."""
    lines = (json.dumps(r.model_dump(mode="json", by_alias=True), sort_keys=True)
             for r in records)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
