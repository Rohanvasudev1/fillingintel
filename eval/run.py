"""Run one arm over the eval set and write a scored results file (Step 5).

Usage (the vector arm needs ``VOYAGE_API_KEY``, ``ANTHROPIC_API_KEY`` and
``DATABASE_URL``; the judges need ``OPENAI_API_KEY`` with ``OPENAI_BASE_URL``
unset; questions not yet in the caches need the network)::

    uv run --env-file .env python -m eval.run --arm vector

The arm answers every question first; then the judges (``eval.judging``) score
each answer three times.  Both go through disk caches, so a rerun replays them.

``--uncached`` repeats a run with fresh answer and judge calls: they go through a
new, empty response cache under ``data/cache/responses-uncached/``, so nothing is
replayed and the shared cache is left as it was.  Query embeddings stay cached,
so retrieval is the same and only the models' answers and verdicts can change.

With ``PHOENIX_COLLECTOR_ENDPOINT`` set, each question's answer is traced to
Phoenix as one tree of spans (``retrieve.tracing``); unset, nothing is traced.

``dev`` is the default split.  ``test`` is scored only with ``--final``, once,
for the final benchmark.  Results go to ``benchmarks/runs/``, which git ignores.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from eval.filter_report import build_filter_report
from eval.judging.report import run_judge_usage
from eval.judging.runner import JUDGES, JudgeRunError, JudgeSpec, judge_all
from eval.judging.scoring import Judge, JudgeConfig, JudgeInput
from eval.question_sets import EVAL_DIR, QuestionSet, QuestionSetError, load_question_sets
from eval.results import (
    Outcome,
    RunInfo,
    build_cells,
    build_header,
    question_record,
    results_path,
    write_results,
)
from ingest.provenance import REPO_ROOT, git_state
from retrieve.arm import Arm, ArmConfig, ArmError, ArmSpec
from retrieve.arms import ARMS
from retrieve.response_cache import DEFAULT_CACHE_DIR as DEFAULT_RESPONSE_CACHE
from retrieve.tracing import (
    QUESTION_SPAN,
    Kind,
    SpanRecorder,
    TracingConfigError,
    build_provider,
    question_attributes,
    status_attributes,
    text_capture,
)

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

RUNS_DIR = REPO_ROOT / "benchmarks" / "runs"
UNCACHED_ROOT = REPO_ROOT / "data" / "cache" / "responses-uncached"
SEED = 20261005  # recorded in every header; seeds every bootstrap interval
PROGRESS_EVERY = 10
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3

TRACER_NAME = "filingintel.eval"

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None, arm_names: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score one arm on the eval set.")
    parser.add_argument("--arm", required=True, choices=sorted(arm_names))
    parser.add_argument("--split", default="dev", choices=("dev", "test"))
    parser.add_argument("--final", action="store_true",
                        help="required to score the test split, for the final benchmark only")
    parser.add_argument("--uncached", action="store_true",
                        help="make every answer and judge call afresh, through a new empty cache")
    return parser.parse_args(argv)


def _usage_problem(args: argparse.Namespace, required_env: Sequence[str],
                   forbidden_env: Sequence[str] = ()) -> str | None:
    if args.split == "test" and not args.final:
        return "the test split is held out; scoring it needs --final (final benchmark only)"
    if args.final and args.split != "test":
        return "--final applies only to --split test"
    return env_problem(required_env, forbidden_env, "eval.run ...")


def env_problem(required_env: Sequence[str], forbidden_env: Sequence[str],
                command: str) -> str | None:
    """Why the environment cannot start *command*, or None: a required variable is unset
    or a forbidden one is set."""
    missing = [name for name in dict.fromkeys(required_env) if not os.environ.get(name)]
    if missing:
        return (f"{', '.join(missing)} not set (run with: uv run --env-file .env "
                f"python -m {command})")
    present = [name for name in dict.fromkeys(forbidden_env) if os.environ.get(name)]
    if present:
        return f"{', '.join(present)} is set; unset it so API keys go only to the vendors' hosts"
    return None


def _run_arm(arm: Arm, sets: Sequence[QuestionSet], split: str,
             spans: SpanRecorder) -> list[Outcome]:
    """Run *arm* on every *split* record of every set; the arm sees only the question text.

    Each question is one trace, rooted in a span that holds the question and its record.
    """
    todo = [(s.name, r) for s in sets for r in s.records if r.split == split]
    outcomes = []
    for done, (set_name, record) in enumerate(todo, start=1):
        metadata = {"question_id": record.id, "set": set_name, "arm": arm.name,
                    "split": split, "class": record.class_}
        attributes = question_attributes(record.question, metadata)
        with spans.span(QUESTION_SPAN, Kind.CHAIN, attributes) as span:
            result = arm.run(record.question)
            span.set_attributes(status_attributes(result.answer.status))
        outcomes.append(Outcome(set_name, record, result))
        if done % PROGRESS_EVERY == 0:
            logger.info("%d of %d questions", done, len(todo))
    return outcomes


def _judge(judge: Judge, outcomes: Sequence[Outcome]) -> list[Outcome]:
    """*outcomes* with every judge run attached."""
    items = [JudgeInput(o.record.question, o.record.class_, o.result.answer, o.result.sources)
             for o in outcomes]
    logger.info("judging %d answers, %d runs each", len(items), judge.config.runs)
    judged = judge_all(judge, items)
    return [replace(o, judged=j) for o, j in zip(outcomes, judged, strict=True)]


def _fresh_cache(root: Path, day: date) -> Path:
    """A new, empty folder under *root* that no other run uses."""
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{day.isoformat()}-", dir=root))


def _score(
    spec: ArmSpec, judges: JudgeSpec, sets: Sequence[QuestionSet], split: str, cache_dir: Path,
    spans: SpanRecorder,
) -> tuple[list[Outcome], ArmConfig, JudgeConfig]:
    """Every *split* question answered by the arm, then judged; raises ArmError or JudgeRunError."""
    with spec.open(cache_dir, spans) as arm:
        outcomes = _run_arm(arm, sets, split, spans)
        config = arm.config
    with judges.open(cache_dir) as judge:
        return _judge(judge, outcomes), config, judge.config


def _document(run: RunInfo, config: ArmConfig, sets: Sequence[QuestionSet],
              judge_config: JudgeConfig, outcomes: Sequence[Outcome]) -> dict[str, Any]:
    """The results file's content: header, cells, filter report and per-question records."""
    return {
        "header": build_header(run, config, sets, judge_config),
        "cells": build_cells(outcomes, run.arm, run.seed, judge_config.runs),
        "filter_report": build_filter_report(outcomes),
        "questions": [question_record(o) for o in outcomes],
        "judge_usage": run_judge_usage([o.judged for o in outcomes if o.judged is not None]),
    }


def main(
    argv: list[str] | None = None,
    *,
    arms: Mapping[str, ArmSpec] = ARMS,
    judges: JudgeSpec = JUDGES,
    eval_dir: Path = EVAL_DIR,
    runs_dir: Path = RUNS_DIR,
    today: date | None = None,
    response_cache: Path = DEFAULT_RESPONSE_CACHE,
    uncached_root: Path = UNCACHED_ROOT,
    tracing: Callable[[Mapping[str, str]], TracerProvider | None] = build_provider,
) -> int:
    """CLI entry point.  Exit codes: 0 ok; 2 bad input; 3 the run could not finish or be
    written (no results file).  *tracing* builds the span provider from the environment."""
    args = _parse_args(argv, list(arms))
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    spec = arms[args.arm]
    problem = _usage_problem(args, [*spec.required_env, *judges.required_env],
                             judges.forbidden_env)
    if problem:
        print(problem, file=sys.stderr)
        return EXIT_USAGE
    try:
        sets = load_question_sets(eval_dir)
        capture = text_capture(os.environ)
        provider = tracing(os.environ)
    except (QuestionSetError, TracingConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    spans = (SpanRecorder(provider.get_tracer(TRACER_NAME), capture) if provider is not None
             else SpanRecorder.off())
    try:
        return _execute(args, spec, judges, sets, spans, runs_dir=runs_dir, today=today,
                        response_cache=response_cache, uncached_root=uncached_root)
    finally:
        if provider is not None:
            provider.shutdown()  # flushes queued spans; a stopped Phoenix costs about a second


def _execute(args: argparse.Namespace, spec: ArmSpec, judges: JudgeSpec,
             sets: Sequence[QuestionSet], spans: SpanRecorder, *, runs_dir: Path,
             today: date | None, response_cache: Path, uncached_root: Path) -> int:
    """Answer, judge and write the results file; returns the exit code."""
    commit = git_state()
    now = datetime.now(UTC)
    day = today or now.date()
    try:
        cache_dir = _fresh_cache(uncached_root, day) if args.uncached else response_cache
    except OSError as exc:
        print(f"could not create a new response cache in {uncached_root}: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    try:
        outcomes, config, judge_config = _score(spec, judges, sets, args.split, cache_dir, spans)
    except (ArmError, JudgeRunError) as exc:
        print(f"run stopped, no results written: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR

    run = RunInfo(now.isoformat(timespec="seconds"), commit, args.arm, args.split,
                  args.final, SEED, args.uncached, cache_dir.name)
    document = _document(run, config, sets, judge_config, outcomes)
    try:
        path = results_path(runs_dir, day, args.arm, args.split, commit)
        write_results(document, path)
    except OSError as exc:
        print(f"could not write results to {runs_dir}: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    excluded = sum(int(r["filter_excluded_gold"]) for r in document["filter_report"].values())
    print(f"{len(outcomes)} questions scored; filter-excluded gold: {excluded}; wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
