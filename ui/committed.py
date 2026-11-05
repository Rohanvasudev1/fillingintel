"""What the committed ``dev`` run files say about a picker option (Step 14).

A sample question shows its per-question metrics from the option's dev run,
labelled as the run labels them (agent-drafted questions, judged uncalibrated).
A live question's estimate is the option's mean answer cost per question in its
full dev run, judges excluded.  Nothing is recomputed.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from statistics import fmean
from types import MappingProxyType

from ingest.provenance import REPO_ROOT
from ui.errors import CommittedRunError
from ui.options import PickerOption

RUNS_DIR = REPO_ROOT / "benchmarks" / "runs"


@dataclass(frozen=True)
class SampleMetrics:
    run_file: str
    class_: str
    retrieval: Mapping[str, float]  # recall and precision at 5 and 10; empty for a decline
    wrong_evidence: bool | None  # None when the question has no hard negatives
    judged: Mapping[str, float] = field(default_factory=dict)  # mean of each judge's runs
    judged_label: str | None = None  # "uncalibrated"; None for a retrieval-only record


def sample_metrics(option: PickerOption, question_id: str,
                   runs_dir: Path = RUNS_DIR) -> SampleMetrics | None:
    """*question_id*'s record in the first of *option*'s dev runs that scored it."""
    for name in option.dev_runs:
        record = _questions(runs_dir / name).get(question_id)
        if record is not None:
            return _metrics(name, record)
    return None


def mean_dev_cost(option: PickerOption, runs_dir: Path = RUNS_DIR) -> float:
    """Mean answer-side USD per question over *option*'s answered dev questions."""
    costs = [q["cost_usd"]["total"] for name in option.dev_runs
             for q in _questions(runs_dir / name).values() if "cost_usd" in q]
    if not costs:
        raise CommittedRunError(f"no answered question in {', '.join(option.dev_runs)}")
    return fmean(costs)


def _metrics(run_file: str, record: Mapping) -> SampleMetrics:
    judged = record.get("judged") or {}
    hits = record.get("wrong_evidence")
    return SampleMetrics(
        run_file=run_file,
        class_=str(record["class"]),
        retrieval=MappingProxyType(dict(record["metrics"])),
        wrong_evidence=None if hits is None else bool(hits),
        judged=MappingProxyType({name: value["mean"]
                                 for name, value in judged.get("metrics", {}).items()}),
        judged_label=judged.get("label"),
    )


@cache
def _questions(path: Path) -> Mapping[str, Mapping]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        return MappingProxyType({q["id"]: q for q in document["questions"]})
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise CommittedRunError(f"cannot read {path.name}: {type(exc).__name__}: {exc}") from exc
