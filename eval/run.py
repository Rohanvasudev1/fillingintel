"""Run one arm over the eval set and write a scored results file (Step 5).

Usage (the vector arm needs ``VOYAGE_API_KEY``, ``ANTHROPIC_API_KEY``,
``DATABASE_URL`` and, for questions not yet in the caches, the network)::

    uv run --env-file .env python -m eval.run --arm vector

The arm answers every question first; then the judges (``eval.judging``) score
each answer three times.  Both go through disk caches, so a rerun replays them.

``--uncached`` repeats a run with fresh answer and judge calls: they go through a
new, empty response cache under ``data/cache/responses-uncached/``, so nothing is
replayed and the shared cache is left as it was.  Query embeddings stay cached,
so retrieval is the same and only the models' answers and verdicts can change.

``dev`` is the default split.  ``test`` is scored only with ``--final``, once,
for the final benchmark.  Results go to ``benchmarks/runs/``, which git ignores.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from eval.filter_report import build_filter_report
from eval.judging.runner import CLAUDE_JUDGES, JudgeRunError, JudgeSpec, judge_all
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

RUNS_DIR = REPO_ROOT / "benchmarks" / "runs"
UNCACHED_ROOT = REPO_ROOT / "data" / "cache" / "responses-uncached"
SEED = 20261005  # recorded in every header; seeds every bootstrap interval
PROGRESS_EVERY = 10
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3

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


def _usage_problem(args: argparse.Namespace, required_env: Sequence[str]) -> str | None:
    if args.split == "test" and not args.final:
        return "the test split is held out; scoring it needs --final (final benchmark only)"
    if args.final and args.split != "test":
        return "--final applies only to --split test"
    missing = [name for name in dict.fromkeys(required_env) if not os.environ.get(name)]
    if missing:
        return (f"{', '.join(missing)} not set (run with: uv run --env-file .env "
                f"python -m eval.run ...)")
    return None


def _run_arm(arm: Arm, sets: Sequence[QuestionSet], split: str) -> list[Outcome]:
    """Run *arm* on every *split* record of every set; the arm sees only the question text."""
    todo = [(s.name, r) for s in sets for r in s.records if r.split == split]
    outcomes = []
    for done, (set_name, record) in enumerate(todo, start=1):
        outcomes.append(Outcome(set_name, record, arm.run(record.question)))
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
    spec: ArmSpec, judges: JudgeSpec, sets: Sequence[QuestionSet], split: str, cache_dir: Path
) -> tuple[list[Outcome], ArmConfig, JudgeConfig]:
    """Every *split* question answered by the arm, then judged; raises ArmError or JudgeRunError."""
    with spec.open(cache_dir) as arm:
        outcomes = _run_arm(arm, sets, split)
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
    }


def main(
    argv: list[str] | None = None,
    *,
    arms: Mapping[str, ArmSpec] = ARMS,
    judges: JudgeSpec = CLAUDE_JUDGES,
    eval_dir: Path = EVAL_DIR,
    runs_dir: Path = RUNS_DIR,
    today: date | None = None,
    response_cache: Path = DEFAULT_RESPONSE_CACHE,
    uncached_root: Path = UNCACHED_ROOT,
) -> int:
    """CLI entry point.  Exit codes: 0 ok; 2 bad input; 3 the run could not finish or be
    written (no results file)."""
    args = _parse_args(argv, list(arms))
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    spec = arms[args.arm]
    problem = _usage_problem(args, [*spec.required_env, *judges.required_env])
    if problem:
        print(problem, file=sys.stderr)
        return EXIT_USAGE
    try:
        sets = load_question_sets(eval_dir)
    except QuestionSetError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    commit = git_state()
    now = datetime.now(UTC)
    day = today or now.date()
    try:
        cache_dir = _fresh_cache(uncached_root, day) if args.uncached else response_cache
    except OSError as exc:
        print(f"could not create a new response cache in {uncached_root}: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    try:
        outcomes, config, judge_config = _score(spec, judges, sets, args.split, cache_dir)
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
