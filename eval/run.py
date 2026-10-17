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

With ``PHOENIX_COLLECTOR_ENDPOINT`` set, each question is traced to Phoenix as
one tree of spans (``retrieve.tracing``): its answer, then its judge calls,
which start under the question's root span after that span has ended.  Unset,
nothing is traced.

``--classes global,decline,unanswerable`` limits the run to those classes; the
header records the selection.  Without it every class runs.

Before the arm opens, its spec's estimate for the selected questions is printed
(``retrieve.run_estimate``); ``--max-cost 2.50`` refuses a run estimated above
$2.50, opening nothing.  ``--retrieval-only`` scores retrieval alone: no answer
or judge call is made, and the file holds retrieval metrics and latency only.
Every answered question records ``total_ms``, the wall time of its whole arm
call, and whether any of its calls replayed from a cache.

``dev`` is the default split.  ``test`` is scored only with ``--final``, once,
for the final benchmark.  Results go to ``benchmarks/runs/``, which git ignores.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

from opentelemetry import trace
from opentelemetry.context import Context

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
from eval.schema import Class, EvalRecord
from ingest.provenance import REPO_ROOT, git_state
from resolve.cli import cost_cap
from retrieve.arm import Arm, ArmConfig, ArmError, ArmSpec, EstimateQuestion
from retrieve.arms import ARMS
from retrieve.pricing import price_table
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
_MS_PER_SECOND = 1000.0

Selected = list[tuple[str, EvalRecord]]  # (question set name, record), in run order

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None, arm_names: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score one arm on the eval set.")
    parser.add_argument("--arm", required=True, choices=sorted(arm_names))
    parser.add_argument("--split", default="dev", choices=("dev", "test"))
    parser.add_argument("--final", action="store_true",
                        help="required to score the test split, for the final benchmark only")
    parser.add_argument("--uncached", action="store_true",
                        help="make every answer and judge call afresh, through a new empty cache")
    parser.add_argument("--classes",
                        help="run only these classes, comma-separated (e.g. global,decline)")
    parser.add_argument("--max-cost", type=cost_cap, metavar="USD",
                        help="refuse a run whose estimate is above this many dollars")
    parser.add_argument("--retrieval-only", action="store_true",
                        help="score retrieval only: no answer or judge call")
    return parser.parse_args(argv)


class ClassSelectionError(ValueError):
    """``--classes`` names no class, or one the schema does not define."""


def parse_classes(text: str) -> tuple[Class, ...]:
    """The classes *text* names, comma-separated, in schema order and without repeats."""
    names = {name.strip() for name in text.split(",")} - {""}
    known = get_args(Class)
    unknown = sorted(names - set(known))
    if unknown:
        raise ClassSelectionError(
            f"--classes: unknown class {', '.join(unknown)}; choose from {', '.join(known)}")
    if not names:
        raise ClassSelectionError(f"--classes names no class; choose from {', '.join(known)}")
    return tuple(c for c in known if c in names)


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


def select(sets: Sequence[QuestionSet], split: str, classes: tuple[Class, ...] | None) -> Selected:
    """The *split* records of every set, or only those of *classes* when given."""
    return [(s.name, r) for s in sets for r in s.records
            if r.split == split and (classes is None or r.class_ in classes)]


def _run_arm(arm: Arm, todo: Selected, split: str, spans: SpanRecorder, *,
             retrieval_only: bool, clock: Callable[[], float],
             ) -> tuple[list[Outcome], list[Context]]:
    """Run *arm* on the *todo* records; the arm sees only the question text.

    Each answered question's ``total_ms`` is the wall time of its whole ``arm.run``
    call; *retrieval_only* calls ``arm.retrieve`` instead and records no total.
    Each question is one trace, rooted in a span that holds the question and its record.
    Returns the outcomes and, for each, a context holding its root span, which the
    judges' spans take as their parent.
    """
    outcomes, parents = [], []
    for done, (set_name, record) in enumerate(todo, start=1):
        metadata = {"question_id": record.id, "set": set_name, "arm": arm.name,
                    "split": split, "class": record.class_}
        attributes = question_attributes(record.question, metadata)
        with spans.span(QUESTION_SPAN, Kind.CHAIN, attributes) as span:
            if retrieval_only:
                outcome = Outcome(set_name, record, arm.retrieve(record.question))
            else:
                start = clock()
                result = arm.run(record.question)
                total_ms = (clock() - start) * _MS_PER_SECOND
                span.set_attributes(status_attributes(result.answer.status))
                outcome = Outcome(set_name, record, result, total_ms=total_ms)
        outcomes.append(outcome)
        parents.append(trace.set_span_in_context(span))
        if done % PROGRESS_EVERY == 0:
            logger.info("%d of %d questions", done, len(todo))
    return outcomes, parents


def _judge(judge: Judge, outcomes: Sequence[Outcome],
           parents: Sequence[Context]) -> list[Outcome]:
    """*outcomes* with every judge run attached; each question's judge spans go under its
    entry in *parents*."""
    items = [JudgeInput(o.record.question, o.record.class_, o.answered.answer,
                        o.answered.sources) for o in outcomes]
    logger.info("judging %d answers, %d runs each", len(items), judge.config.runs)
    judged = judge_all(judge, items, parents=parents)
    return [replace(o, judged=j) for o, j in zip(outcomes, judged, strict=True)]


def _fresh_cache(root: Path, day: date) -> Path:
    """A new, empty folder under *root* that no other run uses."""
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{day.isoformat()}-", dir=root))


def _score(
    spec: ArmSpec, judges: JudgeSpec, todo: Selected, split: str, cache_dir: Path,
    spans: SpanRecorder, *, retrieval_only: bool, clock: Callable[[], float],
) -> tuple[list[Outcome], ArmConfig, JudgeConfig | None]:
    """The *todo* questions answered by the arm, then judged; raises ArmError or JudgeRunError.

    With *retrieval_only* the arm only retrieves, no judge opens, and the judge config
    is None.
    """
    with spec.open(cache_dir, spans) as arm:
        _check_priced(arm.config)
        outcomes, parents = _run_arm(arm, todo, split, spans, retrieval_only=retrieval_only,
                                     clock=clock)
        config = arm.config
    if retrieval_only:
        return outcomes, config, None
    with judges.open(cache_dir, spans) as judge:
        return _judge(judge, outcomes, parents), config, judge.config


def _check_priced(config: ArmConfig) -> None:
    """Refuse an arm whose cost could not be reported, before it answers anything."""
    try:
        price_table(config.models.values())
    except ValueError as exc:
        raise ArmError(str(exc)) from exc


def _document(run: RunInfo, config: ArmConfig, sets: Sequence[QuestionSet],
              judge_config: JudgeConfig | None, outcomes: Sequence[Outcome]) -> dict[str, Any]:
    """The results file's content: header, cells, filter report and per-question records;
    with no judge config (a retrieval-only run), no judge usage."""
    document = {
        "header": build_header(run, config, sets, judge_config),
        "cells": build_cells(outcomes, run.arm, run.seed,
                             judge_config.runs if judge_config is not None else None),
        "filter_report": build_filter_report(outcomes),
        "questions": [question_record(o) for o in outcomes],
    }
    if judge_config is None:
        return document
    return {**document, "judge_usage": run_judge_usage(
        [o.judged for o in outcomes if o.judged is not None])}


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
    clock: Callable[[], float] = time.perf_counter,
) -> int:
    """CLI entry point.  Exit codes: 0 ok; 2 bad input, or an estimate above --max-cost;
    3 the run could not be estimated, finish or be written (no results file).  *tracing*
    builds the span provider from the environment; *clock* times each question."""
    args = _parse_args(argv, list(arms))
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    spec = arms[args.arm]
    judge_required = () if args.retrieval_only else judges.required_env
    judge_forbidden = () if args.retrieval_only else judges.forbidden_env
    problem = _usage_problem(args, [*spec.required_env, *judge_required], judge_forbidden)
    if problem:
        print(problem, file=sys.stderr)
        return EXIT_USAGE
    try:
        classes = parse_classes(args.classes) if args.classes is not None else None
        sets = load_question_sets(eval_dir)
        capture = text_capture(os.environ)
    except (ClassSelectionError, QuestionSetError, TracingConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    todo = select(sets, args.split, classes)
    refused = _check_estimate(spec, todo, args)
    if refused is not None:
        return refused
    try:
        provider = tracing(os.environ)
    except TracingConfigError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    spans = _span_recorder(provider, capture)
    try:
        return _execute(args, spec, judges, sets, classes, todo, spans, runs_dir=runs_dir,
                        today=today, response_cache=response_cache, uncached_root=uncached_root,
                        clock=clock)
    finally:
        if provider is not None:
            provider.shutdown()  # flushes queued spans; a stopped Phoenix costs about a second


def _span_recorder(provider: TracerProvider | None, capture: bool) -> SpanRecorder:
    if provider is None:
        return SpanRecorder.off()
    return SpanRecorder(provider.get_tracer(TRACER_NAME), capture)


def _check_estimate(spec: ArmSpec, todo: Selected, args: argparse.Namespace) -> int | None:
    """Print the arm's estimate for *todo*; the exit code when the run must not start."""
    questions = [EstimateQuestion(r.id, r.class_) for _, r in todo]
    mode = "retrieval only" if args.retrieval_only else "retrieval, answers and judges"
    try:
        usd = spec.estimate(questions, retrieval_only=args.retrieval_only)
    except ValueError as exc:
        print(f"could not estimate the run, nothing was run: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    print(f"estimate: ${usd:.2f} for {len(questions)} questions ({mode}; nothing assumed "
          "to replay from the cache)")
    if args.max_cost is not None and usd > args.max_cost:
        print(f"the estimate ${usd:.2f} is above --max-cost ${args.max_cost:.2f}; "
              "nothing was run", file=sys.stderr)
        return EXIT_USAGE
    return None


def _execute(args: argparse.Namespace, spec: ArmSpec, judges: JudgeSpec,
             sets: Sequence[QuestionSet], classes: tuple[Class, ...] | None, todo: Selected,
             spans: SpanRecorder, *, runs_dir: Path, today: date | None,
             response_cache: Path, uncached_root: Path, clock: Callable[[], float]) -> int:
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
        outcomes, config, judge_config = _score(spec, judges, todo, args.split, cache_dir, spans,
                                                retrieval_only=args.retrieval_only, clock=clock)
    except (ArmError, JudgeRunError) as exc:
        print(f"run stopped, no results written: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR

    run = RunInfo(now.isoformat(timespec="seconds"), commit, args.arm, args.split,
                  args.final, SEED, args.uncached, cache_dir.name, classes,
                  retrieval_only=args.retrieval_only)
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
