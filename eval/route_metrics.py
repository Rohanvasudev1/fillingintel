"""Router accuracy against the eval records' gold classes (Step 13, CONTEXT.md).

Accuracy is over answerable and decline questions: a question counts when its
predicted class equals its gold class, so a decline question counts only when
the router declined it.  Unanswerable questions have no gold arm and are left
out; they get the share of each predicted class instead.  A failed
classification (no class) is wrong and listed.  Every figure is "against
agent-drafted class labels".  ``eval.route`` and ``eval.results_table`` both
read these.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from extract.review_round import wilson
from retrieve.route_prompt import DECLINE, PREDICTED_CLASSES

LABEL = "against agent-drafted class labels"
UNANSWERABLE = "unanswerable"
ERROR = "error"  # the confusion matrix's column for a failed classification
SCORED_CLASSES = PREDICTED_CLASSES  # the gold classes accuracy is read over
PREDICTED_ORDER = (*SCORED_CLASSES, ERROR)


@dataclass(frozen=True)
class Classified:
    id: str
    gold: str
    predicted: str | None  # None when the classifier gave no usable class


def _count(rows: Sequence[Classified]) -> dict[str, object]:
    correct = sum(r.predicted == r.gold for r in rows)
    return {"n": len(rows), "correct": correct, "value": correct / len(rows) if rows else None}


def _ordered(counts: Counter[str]) -> dict[str, int]:
    return {c: counts[c] for c in PREDICTED_ORDER if counts[c]}


def route_metrics(rows: Sequence[Classified]) -> dict[str, object]:
    scored = [r for r in rows if r.gold in SCORED_CLASSES]
    accuracy = _count(scored)
    low, high = wilson(accuracy["correct"], len(scored)) if scored else (None, None)
    unanswerable = [r for r in rows if r.gold == UNANSWERABLE]
    declines = [r for r in rows if r.gold == DECLINE]
    shares = Counter(r.predicted or ERROR for r in unanswerable)
    return {
        "label": LABEL,
        "accuracy": {**accuracy, "low": low, "high": high},
        "per_class": {c: _count([r for r in scored if r.gold == c])
                      for c in SCORED_CLASSES if any(r.gold == c for r in scored)},
        "confusion": {c: _ordered(Counter(r.predicted or ERROR for r in scored if r.gold == c))
                      for c in SCORED_CLASSES if any(r.gold == c for r in scored)},
        "declines": {"n": len(declines), "declined": sum(r.predicted == DECLINE for r in declines),
                     "missed": [r.id for r in declines if r.predicted != DECLINE]},
        "false_declines": [r.id for r in rows if r.gold != DECLINE and r.predicted == DECLINE],
        "unanswerable": {"n": len(unanswerable),
                         "share": {c: n / len(unanswerable)
                                   for c, n in _ordered(shares).items()}},
        "errors": [r.id for r in rows if r.predicted is None],
    }
