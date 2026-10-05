"""Judged scores in the results file: per question and per cell (Step 5, ticket 07).

Each judged metric runs ``JUDGE_RUNS`` times.  A question's score for a metric
is the mean of its runs that produced a value, and its spread is the highest
run minus the lowest.  A cell's mean is the mean of its questions' scores, its
``run_means`` are the cell mean within each run, and its spread is the highest
run mean minus the lowest, which shows how much the judge moves between runs.
The bootstrap interval resamples the questions' scores.  Every judged block
carries the label "uncalibrated" until judge calibration is done.
"""
from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean

from eval.bootstrap import RngFor, mean_interval
from eval.judging.claude import JUDGE_RUNS
from eval.judging.runner import JudgedQuestion
from eval.judging.scoring import RELEVANCY_STRICTNESS, UNCALIBRATED, RunScores

DEFINITIONS = {
    "judged": (
        f"scored by an LLM judge, {UNCALIBRATED}: no judge has been checked against hand "
        f"scores yet; each metric runs {JUDGE_RUNS} times as independent calls; a question's "
        "score is the mean of its runs that produced a value and its spread the highest run "
        "minus the lowest; a cell's mean is over its questions' scores, run_means is the cell mean "
        "within each run, spread is the highest run mean minus the lowest, and the interval "
        "resamples the questions' scores; a run with no value (skipped, undefined, or an "
        "unusable judge reply, which 'errors' counts) is left out"
    ),
    "faithfulness": (
        "Ragas faithfulness: statements in the kept sentences (citation markers removed) that "
        "the judge infers from the retrieved chunks / statements; skipped when no sentence was "
        "kept; no value when the judge finds no statements"
    ),
    "answer_relevancy": (
        "Ragas answer relevancy: mean cosine similarity (Voyage query embeddings) between the "
        f"question and {RELEVANCY_STRICTNESS} questions the judge writes from the shown answer "
        "(citation markers removed), 0 if the judge calls every one noncommittal; skipped "
        "for an empty answer"
    ),
    "citation_support": (
        "kept sentences whose cited chunks, together, support every claim in them, per the "
        "judge / kept sentences; skipped when no sentence was kept"
    ),
    "judged_cost_usd": (
        "what judging cost (Opus judge calls plus Voyage embeddings for answer relevancy), "
        "over all runs; an evaluation cost, kept out of the operational cost_usd, which is "
        "what answering a question costs"
    ),
    "decline_correct": "decline records: 1 if the judge finds the answer declined to advise",
    "not_found_correct": (
        "unanswerable records: 1 if the judge finds the answer said the filings lack the "
        "evidence without stating or guessing the fact"
    ),
}


def _values(judged: JudgedQuestion, metric: str, runs: int) -> list[float | None]:
    """The metric's value in runs 1 to *runs*, None where a run is missing or had none."""
    by_run = {r.run: r.scores.get(metric) for r in judged.runs}
    return [by_run.get(n) for n in range(1, runs + 1)]


def _present(values: Sequence[float | None]) -> list[float]:
    return [v for v in values if v is not None]


def _mean(values: Sequence[float | None]) -> float | None:
    present = _present(values)
    return fmean(present) if present else None


def _spread(values: Sequence[float | None]) -> float | None:
    present = _present(values)
    return max(present) - min(present) if present else None


def _metric_names(judged: Sequence[JudgedQuestion]) -> list[str]:
    return list(dict.fromkeys(m for j in judged for r in j.runs for m in r.scores))


def _totals(runs: Sequence[RunScores]) -> dict[str, float | int]:
    return {"cost_usd": sum(r.cost_usd for r in runs), "calls": sum(r.calls for r in runs),
            "replayed": sum(r.replayed for r in runs)}


def _summary(values: Sequence[float | None]) -> dict[str, object]:
    return {"runs": list(values), "mean": _mean(values), "spread": _spread(values)}


def _errors(judged: JudgedQuestion) -> list[dict[str, object]]:
    return [{"run": r.run, "metric": metric, "error": error}
            for r in judged.runs for metric, error in r.errors.items()]


def question_judged(judged: JudgedQuestion, runs: int) -> dict[str, object]:
    """One question's judge runs, their mean and spread per metric, errors and cost."""
    return {
        "label": UNCALIBRATED,
        "metrics": {m: _summary(_values(judged, m, runs)) for m in _metric_names([judged])},
        "errors": _errors(judged),
        **_totals(judged.runs),
    }


def _metric_cell(judged: Sequence[JudgedQuestion], metric: str, runs: int,
                 rng_for: RngFor) -> dict[str, object]:
    per_question = [_values(j, metric, runs) for j in judged
                    if metric in _metric_names([j])]
    scores = _present([_mean(v) for v in per_question])
    run_means = [_mean([v[r] for v in per_question]) for r in range(runs)]
    return {
        "n": len(scores),
        "mean": fmean(scores) if scores else None,
        "run_means": run_means,
        "spread": _spread(run_means),
        "interval": mean_interval(scores, rng_for(f"judged.{metric}")),
    }


def judged_cell(judged: Sequence[JudgedQuestion], runs: int,
                rng_for: RngFor) -> dict[str, object]:
    """The cell's judged metrics, labelled uncalibrated, with the judges' errors and cost."""
    totals = _totals([r for j in judged for r in j.runs])
    return {
        "label": UNCALIBRATED,
        "runs": runs,
        "metrics": {m: _metric_cell(judged, m, runs, rng_for) for m in _metric_names(judged)},
        "errors": sum(len(r.errors) for j in judged for r in j.runs),
        "calls": totals["calls"],
        "replayed": totals["replayed"],
        "cost_usd": {"total": totals["cost_usd"],
                     "per_query": totals["cost_usd"] / len(judged) if judged else None},
    }
