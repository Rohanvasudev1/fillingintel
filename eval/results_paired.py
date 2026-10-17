"""Paired differences between two run files, as ``eval.compare`` computes them.

``eval.compare.compare_paired`` bootstraps every metric of every class, so one
call per pair of files is kept and read many times.  Answer prompts may differ
(graph_global's reduce prompt, a retrieval-only run's none); judged differences
then compare whole pipelines, as ``eval.compare --allow-answer-prompts`` labels.
"""
from __future__ import annotations

from statistics import fmean

from eval.compare import Paired, PairedDifference, compare_paired
from eval.results_sources import ArmRuns, ResultsInputError, RunFile
from eval.schema import NO_GOLD_CLASSES

_PAIRED: dict[tuple[str, str, str, str], Paired] = {}


def paired(before: RunFile, after: RunFile) -> Paired:
    key = (before.input.path, before.input.sha256, after.input.path, after.input.sha256)
    if key not in _PAIRED:
        _PAIRED[key] = compare_paired(before.document, after.document,
                                      allow_answer_prompts=True)
    return _PAIRED[key]


def paired_difference(before: RunFile, after: RunFile, class_: str,
                      metric: str) -> PairedDifference | None:
    """``after - before`` on *metric* in *class_* (a schema class or ``pooled``)."""
    return next((d for d in paired(before, after).differences
                 if d.class_ == class_ and d.metric == metric), None)


def cost_per_question(runs: ArmRuns) -> float:
    """Mean answer-side USD per answerable question over the arm's full runs, judges
    excluded: what routing a question to the arm spends."""
    costs = [float(q["cost_usd"]["total"]) for f in runs.full_files() for q in f.questions()
             if q["class"] not in NO_GOLD_CLASSES]
    if not costs:
        raise ResultsInputError(f"{runs.arm} has no full run with answerable questions to "
                                "price it")
    return fmean(costs)
