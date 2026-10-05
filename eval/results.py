"""Score an arm's outcomes and build the results file (Step 5).

The file has four parts: a provenance header, ``cells`` keyed by question set,
arm and class, the question filter's report against the labels, and one record
per question.  Each cell holds retrieval metrics, answer and citation counts,
latency per stage, cost and the judged scores, labelled uncalibrated.  Records
without gold chunks (decline, unanswerable) get their own cells, with no
retrieval metrics; their judged scores are decline and not-found correctness.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from functools import partial
from pathlib import Path
from statistics import fmean
from typing import get_args

from eval import answer_report, bootstrap, filter_report, operational, wrong_evidence
from eval.bootstrap import RngFor, interval_rng, is_directional, mean_interval
from eval.judging import report as judged_report
from eval.judging.runner import JudgedQuestion
from eval.judging.scoring import JudgeConfig
from eval.metrics import DEFINITIONS, METRIC_KS, precision_at_k, recall_at_k
from eval.question_sets import QuestionSet
from eval.schema import Class, EvalRecord
from retrieve.arm import ArmConfig, ArmResult
from retrieve.pricing import price_table

CLASS_ORDER = get_args(Class)
NO_GOLD_NOTE = "no gold chunks; retrieval metrics do not apply"
_MAX_RUNS_PER_NAME = 1000


@dataclass(frozen=True)
class Outcome:
    """One question's record, the set it came from, and what the arm returned."""

    question_set: str
    record: EvalRecord
    result: ArmResult
    judged: JudgedQuestion | None = None  # set once the judges have run


def score(record: EvalRecord, result: ArmResult) -> dict[str, float]:
    """Recall and precision at each of ``METRIC_KS``; empty for a record with no gold."""
    if not record.gold_chunk_ids:
        return {}
    retrieved = [r.chunk_id for r in result.retrieved]
    gold = record.gold_chunk_ids
    return {
        f"{name}@{k}": metric(retrieved, gold, k)
        for k in METRIC_KS
        for name, metric in (("recall", recall_at_k), ("precision", precision_at_k))
    }


def hard_negative_hits(outcome: Outcome) -> tuple[wrong_evidence.WrongEvidenceHit, ...]:
    """The hard negatives that ranked above a gold chunk or were cited, for one question."""
    result = outcome.result
    return wrong_evidence.wrong_evidence_hits(
        outcome.record,
        [r.chunk_id for r in result.retrieved],
        wrong_evidence.cited_in(result.answer),
    )


def _retrieval_metrics(
    scored: Sequence[Mapping[str, float]], rng_for: RngFor
) -> dict[str, object]:
    if not any(scored):
        return {"metrics": {}, "intervals": {}, "note": NO_GOLD_NOTE}
    names = list(scored[0])
    return {
        "metrics": {m: fmean(s[m] for s in scored) for m in names},
        "intervals": {m: mean_interval([s[m] for s in scored], rng_for(m)) for m in names},
    }


def _cell(outcomes: Sequence[Outcome], judge_runs: int, rng_for: RngFor) -> dict[str, object]:
    results = [o.result for o in outcomes]
    judged = [o.judged for o in outcomes if o.judged is not None]
    return {
        "n": len(outcomes),
        "directional": is_directional(len(outcomes)),
        **_retrieval_metrics([score(o.record, o.result) for o in outcomes], rng_for),
        "wrong_evidence": wrong_evidence.wrong_evidence_cell(
            [(o.record, hard_negative_hits(o)) for o in outcomes], rng_for),
        "answers": answer_report.answer_cell([r.answer for r in results], rng_for),
        "latency_ms": operational.latency_cell(results, rng_for),
        "cost_usd": operational.cost_cell(results, rng_for),
        "judged": judged_report.judged_cell(judged, judge_runs, rng_for),
    }


def build_cells(
    outcomes: Sequence[Outcome], arm: str, seed: int, judge_runs: int
) -> dict[str, dict[str, dict[str, object]]]:
    """``cells[question_set][arm][class]``: counts, metrics and intervals, classes in schema order.

    Each interval's generator is seeded by *seed* and the interval's set, arm, class and metric.
    """
    cells: dict[str, dict[str, dict[str, object]]] = {}
    for set_name in dict.fromkeys(o.question_set for o in outcomes):
        in_set = [o for o in outcomes if o.question_set == set_name]
        by_class = {
            cls: _cell(
                [o for o in in_set if o.record.class_ == cls],
                judge_runs,
                partial(interval_rng, seed, set_name, arm, cls),
            )
            for cls in CLASS_ORDER
            if any(o.record.class_ == cls for o in in_set)
        }
        cells[set_name] = {arm: by_class}
    return cells


def question_record(outcome: Outcome) -> dict[str, object]:
    """Everything needed to read one question's failure on its own."""
    record, result = outcome.record, outcome.result
    return {
        "id": record.id,
        "question_set": outcome.question_set,
        "class": record.class_,
        "question": record.question,
        "gold_chunk_ids": record.gold_chunk_ids,
        "filter": result.question_filter.as_dict(),
        "filter_matches_labels": filter_report.exact_match(result.question_filter, record),
        "filter_excluded_gold": filter_report.excluded_gold(result.question_filter, record),
        "retrieved": [{"chunk_id": r.chunk_id, "score": r.score} for r in result.retrieved],
        "companies_without_chunks": list(result.companies_without_chunks),
        "retrieval_ms": result.retrieval_ms,
        "embed_ms": result.embed_ms,
        "search_ms": result.search_ms,
        "query_cached": result.query_cached,
        "generation_ms": result.answer.generation_ms,
        "embed_tokens": result.embed_tokens,
        "cost_usd": operational.question_cost(result),
        "metrics": score(record, result),
        "wrong_evidence": (
            [wrong_evidence.hit_record(h) for h in hard_negative_hits(outcome)]
            if wrong_evidence.has_hard_negatives(record) else None
        ),
        "answer": answer_report.answer_record(result.answer),
        "judged": (judged_report.question_judged(outcome.judged, len(outcome.judged.runs))
                   if outcome.judged is not None else None),
    }


@dataclass(frozen=True)
class RunInfo:
    """What the header records beyond the arm's own config."""

    created_at: str
    commit: str
    arm: str
    split: str
    final: bool
    seed: int
    uncached: bool  # True: answers and judges came from fresh API calls, none replayed
    response_cache_dir: str  # the response cache folder's name


def build_header(
    run: RunInfo, config: ArmConfig, question_sets: Sequence[QuestionSet], judge: JudgeConfig
) -> dict[str, object]:
    """The provenance header (invariant 2).  The agent-drafted label is always first."""
    models = {**config.models, "judge": judge.model}
    return {
        "label": question_sets[0].label,
        "created_at": run.created_at,
        "commit": run.commit,
        "arm": run.arm,
        "split": run.split,
        "final": run.final,
        "seed": run.seed,
        "response_cache": {"uncached": run.uncached, "dir": run.response_cache_dir},
        "bootstrap": {"resamples": bootstrap.BOOTSTRAP_RESAMPLES,
                      "confidence": bootstrap.CONFIDENCE},
        "models": models,
        "efforts": {**config.efforts, "judge": judge.effort},
        "answer_prompt": {
            "version": config.answer_prompt.version,
            "sha256": config.answer_prompt.sha256,
            "path": config.answer_prompt.path,
        },
        "answer_max_tokens": config.answer_max_tokens,
        "prices": price_table(models.values()),
        "judging": judge.as_header(),
        "k": config.k,
        "metric_ks": list(METRIC_KS),
        "chunker_version": config.chunker_version,
        "question_sets": [
            {
                "name": s.name,
                "label": s.label,
                "path": s.file,
                "sha256": s.sha256,
                "records_in_split": sum(r.split == run.split for r in s.records),
            }
            for s in question_sets
        ],
        "metric_definitions": (DEFINITIONS | wrong_evidence.DEFINITIONS
                               | filter_report.DEFINITIONS | answer_report.DEFINITIONS
                               | operational.DEFINITIONS | bootstrap.DEFINITIONS
                               | judged_report.DEFINITIONS),
    }


def results_path(runs_dir: Path, day: date, arm: str, split: str, commit: str) -> Path:
    """``{date}-{arm}-{split}-{commit}.json``, or with ``-2``, ``-3``... if that exists."""
    stem = f"{day.isoformat()}-{arm}-{split}-{commit}"
    for n in range(1, _MAX_RUNS_PER_NAME + 1):
        path = runs_dir / (f"{stem}.json" if n == 1 else f"{stem}-{n}.json")
        if not path.exists():
            return path
    raise FileExistsError(f"{_MAX_RUNS_PER_NAME} runs named {stem} already exist in {runs_dir}")


def write_results(document: Mapping[str, object], path: Path) -> None:
    """Write *document* as JSON; refuses to replace an existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
