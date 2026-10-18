"""``python -m eval.route``: the router's classifier alone on dev (Step 13).

Classifies every ``dev`` record (answerable, decline and unanswerable) with
claude-haiku-5-5 at ``--effort`` under prompt ``--prompt``, and runs no arm.
Answerable and decline records give router accuracy with a Wilson 95%
interval, the confusion matrix and the declines missed; every question the
router declined that is not a decline question is a false decline; unanswerable
records get the share of each predicted class (``eval.route_metrics``).  A
reply cut off, refused or off the schema is recorded as an error and counted
wrong; a failed API call stops the run.

Calls go through the response cache, so ``eval.run --arm router`` later replays
them.  The estimate prices only requests the cache lacks and is printed first;
the run is refused above ``--max-cost`` (default $1) or when it would take
Step 13 past its cap with the committed spend (``eval.step13_spend``).  The
report goes to ``benchmarks/router/route-run{N}.json``.  ``test`` is refused:
it is classified only inside ``eval.run --final``.

    env -u ANTHROPIC_BASE_URL uv run --env-file .env python -m eval.route --effort low

Exit codes: 0 written; 2 bad arguments or refused; 3 could not run.
"""
from __future__ import annotations

import argparse
import contextvars
import os
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from eval.question_sets import EVAL_DIR, QuestionSet, QuestionSetError, load_question_sets
from eval.route_metrics import LABEL, Classified, route_metrics
from eval.schema import EvalRecord
from eval.step13_spend import ROUTER_DIR, STEP_13_CAP_USD, spent_usd
from graph.load_report import next_report_path
from ingest.atomic_json import write_json_atomic
from ingest.provenance import git_state
from resolve.cli import cost_cap
from retrieve.answer_model import (
    API_KEY_ENV,
    AnswerModel,
    AnswerModelError,
    AnthropicAnswerModel,
    ApiResponse,
    ModelRequest,
)
from retrieve.pricing import price_table
from retrieve.query_cache import CacheError
from retrieve.response_cache import DEFAULT_CACHE_DIR, CachedAnswerModel
from retrieve.route_classifier import (
    EFFORT_CHOICES,
    ROUTE_EFFORT,
    ROUTE_MAX_TOKENS,
    ROUTE_MODEL,
    ROUTE_PRICES,
    ROUTE_PROMPT_VERSION,
    Classification,
    RouteClassifier,
    RouteError,
    build_route_request,
    estimate_request_usd,
)
from retrieve.route_prompt import PUBLISHED, RoutePrompt, RoutePromptError, load_route_prompt

EXIT_OK, EXIT_USAGE, EXIT_COULD_NOT_RUN = 0, 2, 3
DEFAULT_MAX_COST = 1.0
WORKERS = 4
RUN_PREFIX = "route-run"
BASE_URL_ENV = "ANTHROPIC_BASE_URL"
QUESTIONS_LABEL = "agent-drafted questions, dev"


class _NoCalls:
    """Stands in for the API when every request is cached; a call is a bug."""

    def complete(self, request: ModelRequest) -> ApiResponse:
        raise AnswerModelError("a cached request was not in the response cache")


@dataclass(frozen=True)
class _Row:
    record: EvalRecord
    found: Classification | None
    error: str | None = None


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify the dev questions; run no arm.")
    parser.add_argument("--effort", choices=EFFORT_CHOICES, default=ROUTE_EFFORT)
    parser.add_argument("--prompt", choices=sorted(PUBLISHED), default=ROUTE_PROMPT_VERSION)
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--max-cost", type=cost_cap, default=DEFAULT_MAX_COST, metavar="USD")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, eval_dir: Path = EVAL_DIR,
         out_dir: Path = ROUTER_DIR, response_cache: Path = DEFAULT_CACHE_DIR,
         model: AnswerModel | None = None, spent: Callable[[], float] = spent_usd) -> int:
    """*model* replaces the Anthropic client (tests); *spent* reads committed spend."""
    try:
        args = _parse(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    if args.split == "test":
        print("test is held out: it is classified only inside eval.run --final",
              file=sys.stderr)
        return EXIT_USAGE
    try:
        sets = load_question_sets(eval_dir)
        prompt = load_route_prompt(args.prompt)
        committed = spent()
    except (QuestionSetError, RoutePromptError, ValueError) as exc:
        print(f"could not start: {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    records = [r for s in sets for r in s.records if r.split == args.split]
    estimate = _estimate(prompt, records, args.effort, response_cache)
    print(f"estimate: ${estimate:.4f} for {len(records)} questions at effort {args.effort} "
          f"under {prompt.prompt_version} (cached requests cost nothing); "
          f"Step 13 spent ${committed:.2f} of ${STEP_13_CAP_USD:.2f}")
    refused = _refusal(estimate, committed, args.max_cost)
    if refused is not None:
        print(refused, file=sys.stderr)
        return EXIT_USAGE
    try:
        rows, spent_now = _classify_all(records, prompt, args.effort, response_cache, model,
                                        fresh_calls=estimate > 0)
    except (AnswerModelError, CacheError, OSError) as exc:
        print(f"run stopped, no report written: {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    document = _report(args, prompt, sets, rows, estimate, spent_now)
    path = next_report_path(out_dir, RUN_PREFIX)
    write_json_atomic(path, document)
    accuracy = document["metrics"]["accuracy"]
    print(f"accuracy {accuracy['correct']} of {accuracy['n']} ({LABEL}); wrote {path}")
    return EXIT_OK


def _refusal(estimate: float, committed: float, max_cost: float) -> str | None:
    if estimate > max_cost:
        return f"the estimate ${estimate:.4f} is above --max-cost ${max_cost:.2f}; nothing sent"
    if committed + estimate > STEP_13_CAP_USD:
        return (f"${committed:.2f} spent plus ${estimate:.4f} would pass the Step 13 cap of "
                f"${STEP_13_CAP_USD:.2f}; nothing sent")
    return None


def _estimate(prompt: RoutePrompt, records: Sequence[EvalRecord], effort: str,
              response_cache: Path) -> float:
    cache = CachedAnswerModel(_NoCalls(), response_cache)
    requests = [build_route_request(prompt, r.question, effort) for r in records]
    return sum(estimate_request_usd(q) for q in requests if not cache.contains(q))


def _classify_all(records: Sequence[EvalRecord], prompt: RoutePrompt, effort: str,
                  response_cache: Path, model: AnswerModel | None,
                  fresh_calls: bool) -> tuple[list[_Row], float]:
    with ExitStack() as stack:
        inner = model
        if inner is None:
            inner = (_live_model(stack) if fresh_calls else _NoCalls())
        classifier = RouteClassifier(CachedAnswerModel(inner, response_cache), prompt, effort)
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(contextvars.copy_context().run, _classify_one, classifier, r)
                       for r in records]
            try:
                rows = [f.result() for f in futures]
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
    spent_now = sum(r.found.cost.cost_usd for r in rows
                    if r.found is not None and not r.found.cost.from_cache)
    return rows, spent_now


def _live_model(stack: ExitStack) -> AnswerModel:
    if os.environ.get(BASE_URL_ENV):
        raise AnswerModelError(f"{BASE_URL_ENV} is set; unset it so the key goes only to "
                               "Anthropic (env -u ANTHROPIC_BASE_URL ...)")
    if not os.environ.get(API_KEY_ENV):
        raise AnswerModelError(f"{API_KEY_ENV} is not set (uv run --env-file .env ...)")
    return stack.enter_context(AnthropicAnswerModel.from_env())


def _classify_one(classifier: RouteClassifier, record: EvalRecord) -> _Row:
    try:
        return _Row(record, classifier.classify(record.question))
    except RouteError as exc:
        return _Row(record, None, str(exc))


def _report(args: argparse.Namespace, prompt: RoutePrompt, sets: Sequence[QuestionSet],
            rows: Sequence[_Row],
            estimate: float, spent_now: float) -> dict[str, object]:
    classified = [Classified(r.record.id, r.record.class_,
                             r.found.predicted_class if r.found else None) for r in rows]
    return {
        "label": f"{QUESTIONS_LABEL}; router accuracy {LABEL}",
        "commit": git_state(),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "split": args.split,
        "model": ROUTE_MODEL,
        "effort": args.effort,
        "max_tokens": ROUTE_MAX_TOKENS,
        "prompt": {"version": prompt.prompt_version, "sha256": prompt.sha256,
                   "path": prompt.path},
        "prices": price_table([ROUTE_MODEL], ROUTE_PRICES),
        "question_sets": [{"name": s.name, "sha256": s.sha256} for s in sets],
        "estimate_usd": estimate,
        "cost_usd": sum(r.found.cost.cost_usd for r in rows if r.found is not None),
        "spent_usd": spent_now,
        "metrics": route_metrics(classified),
        "questions": [_question(r) for r in rows],
    }


def _question(row: _Row) -> dict[str, object]:
    found = row.found
    return {
        "id": row.record.id,
        "class": row.record.class_,
        "predicted_class": found.predicted_class if found else None,
        "correct": found is not None and found.predicted_class == row.record.class_,
        "reason": found.reason if found else None,
        "error": row.error,
        "from_cache": found.cost.from_cache if found else None,
        "api_ms": found.cost.ms if found else None,
        "cost_usd": found.cost.cost_usd if found else None,
        "output_tokens": found.cost.output_tokens if found else None,
    }


if __name__ == "__main__":
    sys.exit(main())
