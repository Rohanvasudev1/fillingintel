"""The dev-best routing map (Step 13, RESEARCH-PLAN amendment of 2026-10-16).

Per answerable class: the arm with the highest mean dev recall@10 is the best;
an arm whose paired recall@10 difference against the best has a 95% interval
including 0 ties with it; among the tied arms the lowest dev cost per question
wins, an exact cost tie going to the earlier arm in ``ARMS``.  Cost is the
answer side only (``eval.results_paired.cost_per_question``); a retrieval-only
class takes the arm's full-run cost.

``python -m eval.results_table --dev-best`` writes the map once to
``benchmarks/router/dev-best-map.json`` with the numbers that decided it; the
router (ticket 03) reads that file.  The map is always labelled "tuned on dev".
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from eval.compare import PairedDifference
from eval.gate_scores import GATED_CLASSES
from eval.results_inputs import ARMS
from eval.results_paired import cost_per_question, paired_difference
from eval.results_sources import ResultsInputError, Split

RECALL_10 = "metrics.recall@10"
LABEL = "tuned on dev"
RULE = ("per answerable class, the arm with the highest mean dev recall@10; an arm whose "
        "paired recall@10 difference against it has a 95% interval including 0 ties with "
        "it; among tied arms the lowest dev cost per question (answer side, judges excluded) "
        "wins, an exact cost tie going to the earlier arm")


class DevBestError(ValueError):
    """The map cannot be chosen or written."""


@dataclass(frozen=True)
class Contender:
    arm: str
    recall_at_10: float
    difference: PairedDifference | None  # this arm minus the best; None for the best
    cost_per_question: float
    tied: bool
    source: str = ""  # the run file its recall came from


@dataclass(frozen=True)
class ClassChoice:
    class_: str
    best: str
    chosen: str
    contenders: tuple[Contender, ...]


def best_arm(recalls: Mapping[str, float]) -> str:
    """The arm with the highest recall, an exact tie going to the earlier arm."""
    arms = list(recalls)
    return max(arms, key=lambda a: (recalls[a], -arms.index(a)))


def choose_class(class_: str, recalls: Mapping[str, float],
                 differences: Mapping[str, PairedDifference],
                 costs: Mapping[str, float],
                 sources: Mapping[str, str] | None = None) -> ClassChoice:
    """The rule on one class.  *differences* holds each arm's difference against the
    arm with the highest recall (``best``); arms are taken in *recalls*' order."""
    arms = list(recalls)
    best = best_arm(recalls)
    contenders = tuple(
        Contender(a, recalls[a], None if a == best else differences[a], costs[a],
                  tied=a == best or not differences[a].counts,
                  source=(sources or {}).get(a, ""))
        for a in arms)
    tied = [c for c in contenders if c.tied]
    chosen = min(tied, key=lambda c: (c.cost_per_question, arms.index(c.arm))).arm
    return ClassChoice(class_, best, chosen, contenders)


def dev_best_choices(split: Split) -> tuple[ClassChoice, ...]:
    """The rule on every answerable class the dev runs hold, in schema order."""
    costs = {arm: cost_per_question(runs) for arm, runs in split.items()}
    choices = []
    for class_ in GATED_CLASSES:
        sources = {arm: runs.source(class_) for arm, runs in split.items()}
        sources = {a: s for a, s in sources.items() if s is not None}
        if not sources:
            continue
        recalls = {a: float(s.cell(class_)["metrics"]["recall@10"]) for a, s in sources.items()}
        best = best_arm(recalls)
        differences = {}
        for arm, source in sources.items():
            if arm == best:
                continue
            found = paired_difference(sources[best], source, class_, RECALL_10)
            if found is None:
                raise DevBestError(f"{arm} and {best} share no scored {class_} question")
            differences[arm] = found
        choices.append(choose_class(class_, recalls, differences, costs,
                                    {a: s.input.path for a, s in sources.items()}))
    return tuple(choices)


def map_document(choices: Sequence[ClassChoice], split: Split,
                 commit: str) -> dict[str, object]:
    """The committed map: the choice per class and every number behind it."""
    unknown = sorted({c.chosen for c in choices} - set(ARMS))
    if unknown:
        raise DevBestError(f"unknown arms {unknown}")
    return {
        "commit": commit,
        "label": LABEL,
        "questions": "agent-drafted questions, dev, answerable only",
        "rule": RULE,
        "map": {c.class_: c.chosen for c in choices},
        "classes": {c.class_: {
            "best": c.best,
            "chosen": c.chosen,
            "contenders": [_contender(x) for x in c.contenders],
        } for c in choices},
        "inputs": [{"path": f.input.path, "sha256": f.input.sha256, "commit": f.input.commit}
                   for runs in split.values() for f in runs.files],
    }


def _contender(c: Contender) -> dict[str, object]:
    difference = None if c.difference is None else {
        "n": c.difference.n, "mean": c.difference.mean,
        "low": c.difference.low, "high": c.difference.high}
    return {"arm": c.arm, "source": c.source, "recall@10": c.recall_at_10,
            "difference_vs_best": difference, "cost_per_question_usd": c.cost_per_question,
            "tied": c.tied}


def write_map(document: Mapping[str, object], path: Path) -> None:
    """Write the map once; refuses when *path* exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8") as fh:
            json.dump(document, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
    except FileExistsError as exc:
        raise DevBestError(f"{path} already exists; the dev-best map is written once") from exc


def read_map(document: object) -> Mapping[str, str]:
    """The class-to-arm map from a committed map file's content."""
    if not isinstance(document, Mapping) or not isinstance(document.get("map"), Mapping):
        raise ResultsInputError("the dev-best map file has no map")
    return dict(document["map"])
