"""Score an arm's outcomes and build the results file (Step 5).

The file has four parts: a provenance header, ``cells`` keyed by question set,
arm and class, the question filter's report against the labels, and one record
per question.  Each cell holds retrieval metrics, answer and citation counts,
latency per stage, cost and the judged scores, labelled uncalibrated.  Records
without gold chunks (decline, unanswerable) get their own cells, with no
retrieval metrics; their judged scores are decline and not-found correctness.

A retrieval-only run (Step 13) has no answers: its cells hold retrieval metrics,
the wrong-evidence rate by rank alone and retrieval latency, and its records and
header hold no answer, judged or cost fields.  An answered question also
records ``total_ms``, the wall time of its whole arm call, and ``replayed``.
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

from eval import (
    answer_report,
    bootstrap,
    filter_report,
    graph_report,
    operational,
    wrong_evidence,
)
from eval.bootstrap import RngFor, interval_rng, is_directional, mean_interval
from eval.judging import report as judged_report
from eval.judging.runner import JudgedQuestion
from eval.judging.scoring import JudgeConfig
from eval.metrics import DEFINITIONS, METRIC_KS, precision_at_k, recall_at_k
from eval.question_sets import QuestionSet
from eval.schema import Class, EvalRecord
from retrieve.arm import ArmConfig, ArmResult, Retrieval, RetrievedChunk
from retrieve.pricing import price_table

CLASS_ORDER = get_args(Class)
NO_GOLD_NOTE = "no gold chunks; retrieval metrics do not apply"
MODE_DEFINITIONS = {
    "mode": (
        "full: retrieval, answers and judges; retrieval_only: retrieval alone, with no answer "
        "or judge call, so cells hold retrieval metrics, retrieval latency and the "
        "wrong-evidence rate from ranks alone (nothing is cited), and no answer, judged, cost "
        "or total_ms fields"
    ),
}
_MAX_RUNS_PER_NAME = 1000


@dataclass(frozen=True)
class Outcome:
    """One question's record, the set it came from, and what the arm returned."""

    question_set: str
    record: EvalRecord
    result: Retrieval  # an ArmResult, unless the run was retrieval-only
    judged: JudgedQuestion | None = None  # set once the judges have run
    total_ms: float | None = None  # the whole arm call's wall time; None retrieval-only

    @property
    def answered(self) -> ArmResult:
        """The arm's result with its answer; TypeError for a retrieval-only outcome."""
        if not isinstance(self.result, ArmResult):
            raise TypeError(f"{self.record.id} was retrieved only; it has no answer")
        return self.result


def score(record: EvalRecord, result: Retrieval) -> dict[str, float]:
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
    """The hard negatives that ranked above a gold chunk or were cited, for one question;
    a retrieval-only question cites nothing."""
    result = outcome.result
    cited = (wrong_evidence.cited_in(result.answer) if isinstance(result, ArmResult)
             else set())
    return wrong_evidence.wrong_evidence_hits(
        outcome.record, [r.chunk_id for r in result.retrieved], cited)


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


def _cell(outcomes: Sequence[Outcome], judge_runs: int | None,
          rng_for: RngFor) -> dict[str, object]:
    """One cell; with no *judge_runs* (a retrieval-only run), retrieval fields only."""
    results = [o.result for o in outcomes]
    graph = graph_report.graph_cell(results)
    retrieval = {
        "n": len(outcomes),
        "directional": is_directional(len(outcomes)),
        **_retrieval_metrics([score(o.record, o.result) for o in outcomes], rng_for),
        "wrong_evidence": wrong_evidence.wrong_evidence_cell(
            [(o.record, hard_negative_hits(o)) for o in outcomes], rng_for),
    }
    graph_part = {"graph": graph} if graph is not None else {}
    if judge_runs is None:
        return {**retrieval, "latency_ms": operational.latency_cell(results, rng_for),
                **graph_part}
    answered = [o.answered for o in outcomes]
    judged = [o.judged for o in outcomes if o.judged is not None]
    return {
        **retrieval,
        "answers": answer_report.answer_cell([r.answer for r in answered], rng_for),
        "latency_ms": operational.latency_cell(answered, rng_for),
        "total_ms": operational.total_cell(
            [(o.total_ms, operational.replayed(o.answered)) for o in outcomes], rng_for),
        "cost_usd": operational.cost_cell(answered, rng_for),
        "judged": judged_report.judged_cell(judged, judge_runs, rng_for),
        **graph_part,
    }


def build_cells(
    outcomes: Sequence[Outcome], arm: str, seed: int, judge_runs: int | None
) -> dict[str, dict[str, dict[str, object]]]:
    """``cells[question_set][arm][class]``: counts, metrics and intervals, classes in schema order.

    Each interval's generator is seeded by *seed* and the interval's set, arm, class and metric.
    *judge_runs* is None for a retrieval-only run.
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
    """Everything needed to read one question's failure on its own; a retrieval-only
    question has no answer, judged, cost or end-to-end fields."""
    result = outcome.result
    retrieval = _retrieval_record(outcome)
    if not isinstance(result, ArmResult):
        return retrieval
    fields = {
        **retrieval,
        "generation_ms": result.answer.generation_ms,
        "total_ms": outcome.total_ms,
        "replayed": operational.replayed(result),
        "cost_usd": operational.question_cost(result),
        "answer": answer_report.answer_record(result.answer),
        "judged": (judged_report.question_judged(outcome.judged, len(outcome.judged.runs))
                   if outcome.judged is not None else None),
    }
    return {name: fields[name] for name in _FULL_RECORD_ORDER if name in fields}


def _retrieval_record(outcome: Outcome) -> dict[str, object]:
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
        "retrieved": [_retrieved_record(r) for r in result.retrieved],
        "companies_without_chunks": list(result.companies_without_chunks),
        "retrieval_ms": result.retrieval_ms,
        "embed_ms": result.embed_ms,
        "search_ms": result.search_ms,
        "query_cached": result.query_cached,
        "embed_tokens": result.embed_tokens,
        "metrics": score(record, result),
        "wrong_evidence": (
            [wrong_evidence.hit_record(h) for h in hard_negative_hits(outcome)]
            if wrong_evidence.has_hard_negatives(record) else None
        ),
        **({"graph": graph_report.question_graph(result.graph)}
           if result.graph is not None else {}),
    }


# An answered record's fields in the order files had before Step 13, end-to-end time added.
_FULL_RECORD_ORDER = (
    "id", "question_set", "class", "question", "gold_chunk_ids", "filter",
    "filter_matches_labels", "filter_excluded_gold", "retrieved", "companies_without_chunks",
    "retrieval_ms", "embed_ms", "search_ms", "query_cached", "generation_ms", "total_ms",
    "replayed", "embed_tokens", "cost_usd", "metrics", "wrong_evidence", "answer", "judged",
    "graph",
)


def _retrieved_record(chunk: RetrievedChunk) -> dict[str, object]:
    """A retrieved chunk's ID and score, and its best evidence confidence when the arm gave one."""
    record: dict[str, object] = {"chunk_id": chunk.chunk_id, "score": chunk.score}
    if chunk.confidence is not None:
        record["confidence"] = chunk.confidence.value
    return record


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
    classes: tuple[Class, ...] | None = None  # the --classes selection; None runs every class
    retrieval_only: bool = False  # --retrieval-only: no answer or judge call


def build_header(
    run: RunInfo, config: ArmConfig, question_sets: Sequence[QuestionSet],
    judge: JudgeConfig | None,
) -> dict[str, object]:
    """The provenance header (invariant 2).  The agent-drafted label is always first.

    A retrieval-only run has no *judge*: its header has no answer prompt, prices or
    judge entries, since it makes no answer or judge call.
    """
    judge_model = {"judge": judge.model} if judge is not None else {}
    models = {**config.models, **judge_model}
    return {
        "label": question_sets[0].label,
        "created_at": run.created_at,
        "commit": run.commit,
        "arm": run.arm,
        "split": run.split,
        "final": run.final,
        "mode": "retrieval_only" if run.retrieval_only else "full",
        **({"classes": list(run.classes)} if run.classes is not None else {}),
        "seed": run.seed,
        "response_cache": {"uncached": run.uncached, "dir": run.response_cache_dir},
        "bootstrap": {"resamples": bootstrap.BOOTSTRAP_RESAMPLES,
                      "confidence": bootstrap.CONFIDENCE},
        "models": models,
        "efforts": {**config.efforts,
                    **({"judge": judge.effort} if judge is not None else {})},
        **(_answering(config, models, judge) if judge is not None else {}),
        "k": config.k,
        "metric_ks": list(METRIC_KS),
        "chunker_version": config.chunker_version,
        **({"graph": graph_report.header_graph(config.graph)} if config.graph is not None
           else {}),
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
                               | judged_report.DEFINITIONS
                               | _graph_definitions(config) | MODE_DEFINITIONS),
    }


def _answering(config: ArmConfig, models: Mapping[str, str],
               judge: JudgeConfig) -> dict[str, object]:
    """The header's answer prompt, prices and judging, for a run that answers."""
    return {
        "answer_prompt": {
            "version": config.answer_prompt.version,
            "sha256": config.answer_prompt.sha256,
            "path": config.answer_prompt.path,
        },
        "answer_max_tokens": config.answer_max_tokens,
        "prices": price_table(models.values()),
        "judging": judge.as_header(),
    }


def _graph_definitions(config: ArmConfig) -> dict[str, str]:
    """A graph arm's definitions; graph_global's stages and map-reduce replace the entity
    arms' stage text."""
    if config.graph is None:
        return {}
    if config.graph.global_search is not None:
        return (graph_report.DEFINITIONS | graph_report.GLOBAL_DEFINITIONS
                | operational.GLOBAL_DEFINITIONS)
    return graph_report.DEFINITIONS | operational.GRAPH_DEFINITIONS


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
