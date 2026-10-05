"""Run one arm over the eval set and write a scored results file (Step 5).

Usage (the vector arm needs ``VOYAGE_API_KEY``, ``DATABASE_URL`` and, for
questions not yet in the query cache, the network)::

    uv run --env-file .env python -m eval.run --arm vector

``dev`` is the default split.  ``test`` is scored only with ``--final``, once,
for the final benchmark.  Results go to ``benchmarks/runs/``, which git ignores.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from eval.filter_report import build_filter_report
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
from retrieve.arm import Arm, ArmError, ArmSpec
from retrieve.arms import ARMS

RUNS_DIR = REPO_ROOT / "benchmarks" / "runs"
SEED = 20261005  # recorded in every header; the bootstrap (ticket 06) draws from it
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
    return parser.parse_args(argv)


def _usage_problem(args: argparse.Namespace, spec: ArmSpec) -> str | None:
    if args.split == "test" and not args.final:
        return "the test split is held out; scoring it needs --final (final benchmark only)"
    if args.final and args.split != "test":
        return "--final applies only to --split test"
    missing = [name for name in spec.required_env if not os.environ.get(name)]
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


def main(
    argv: list[str] | None = None,
    *,
    arms: Mapping[str, ArmSpec] = ARMS,
    eval_dir: Path = EVAL_DIR,
    runs_dir: Path = RUNS_DIR,
    today: date | None = None,
) -> int:
    """CLI entry point.  Exit codes: 0 ok; 2 bad input; 3 the arm failed (no file written)."""
    args = _parse_args(argv, list(arms))
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    spec = arms[args.arm]
    problem = _usage_problem(args, spec)
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
    try:
        with spec.open() as arm:
            outcomes = _run_arm(arm, sets, args.split)
            config = arm.config
    except ArmError as exc:
        print(f"run stopped, no results written: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR

    run = RunInfo(now.isoformat(timespec="seconds"), commit, args.arm, args.split,
                  args.final, SEED)
    filters = build_filter_report(outcomes)
    document = {
        "header": build_header(run, config, sets),
        "cells": build_cells(outcomes, args.arm),
        "filter_report": filters,
        "questions": [question_record(o) for o in outcomes],
    }
    try:
        path = results_path(runs_dir, today or now.date(), args.arm, args.split, commit)
        write_results(document, path)
    except OSError as exc:
        print(f"could not write results to {runs_dir}: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    excluded = sum(int(r["filter_excluded_gold"]) for r in filters.values())
    print(f"{len(outcomes)} questions scored; filter-excluded gold: {excluded}; wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
