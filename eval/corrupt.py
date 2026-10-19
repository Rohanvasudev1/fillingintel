"""``python -m eval.corrupt``: the corruption test (Step 13 ticket 04).

Draws 30 answerable dev answers by its own seed, leaving out every question of
the calibration draw (``benchmarks/calibration/draw.json``, which must exist),
plants the four errors of ``eval.calibration.corruptors`` in each, and judges
every corrupted copy for faithfulness and citation support with the pinned
gpt-6-luna judge, 3 runs, through the response cache.  Catch rates per error
kind, with Wilson intervals, go to ``benchmarks/calibration/corruption-run{N}.json``,
labelled "corruption test, not calibration".

The estimate is printed first, priced from the drawn answers' measured judge
costs; the run is refused above ``--max-cost`` (default $1) or when it would take
Step 13 past its cap with the committed spend.

    env -u ANTHROPIC_BASE_URL uv run --env-file .env python -m eval.corrupt

Needs ``data/parsed`` for chunk text, OPENAI_API_KEY and VOYAGE_API_KEY, with
OPENAI_BASE_URL unset.  Exit codes: 0 written; 2 refused or bad arguments;
3 could not run.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from eval.calibration.answers import (
    RUNS_DIR,
    AnswerFileError,
    RunAnswer,
    chunk_texts,
    load_answers,
)
from eval.calibration.catch import LABEL, catch_rates
from eval.calibration.corruption import (
    METRICS,
    Planned,
    estimate_usd,
    fresh_usd,
    judge_planned,
    plan,
)
from eval.calibration.draw import (
    CALIBRATION_DIR,
    DRAW_PATH,
    DrawError,
    draw,
    read_draw,
)
from eval.judging.runner import JUDGES, JudgeRunError, open_judges
from eval.judging.scoring import JudgeConfig
from eval.run import env_problem
from eval.step13_spend import STEP_13_CAP_USD, spent_usd
from graph.load_report import next_report_path
from ingest.atomic_json import write_json_atomic
from ingest.parsed_files import DEFAULT_PARSED_DIR
from ingest.provenance import git_state
from resolve.cli import cost_cap
from retrieve.response_cache import DEFAULT_CACHE_DIR

EXIT_OK, EXIT_USAGE, EXIT_COULD_NOT_RUN = 0, 2, 3
CORRUPTION_SEED = 20261018
ANSWERS = 30
DEFAULT_MAX_COST = 1.0
RUN_PREFIX = "corruption-run"


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plant known errors and count the judge's "
                                                 "catches.")
    parser.add_argument("--max-cost", type=cost_cap, default=DEFAULT_MAX_COST, metavar="USD")
    parser.add_argument("--parsed-dir", type=Path, default=DEFAULT_PARSED_DIR)
    return parser.parse_args(argv)


def corruption_draw(pool: Sequence[RunAnswer], calibration_draw: Path = DRAW_PATH
                    ) -> tuple[RunAnswer, ...]:
    """The corruption test's answers: disjoint by question from the calibration draw."""
    taken = frozenset(e.question_id for e in read_draw(calibration_draw).entries)
    return draw(pool, CORRUPTION_SEED, answers=ANSWERS, behaviours=0, exclude_questions=taken)


def _refusal(estimate: float, committed: float, max_cost: float) -> str | None:
    if estimate > max_cost:
        return f"the estimate ${estimate:.4f} is above --max-cost ${max_cost:.2f}; nothing sent"
    if committed + estimate > STEP_13_CAP_USD:
        return (f"${committed:.2f} spent plus ${estimate:.4f} would pass the Step 13 cap of "
                f"${STEP_13_CAP_USD:.2f}; nothing sent")
    return None


def main(argv: Sequence[str] | None = None, *, runs_dir: Path = RUNS_DIR,
         out_dir: Path = CALIBRATION_DIR, response_cache: Path = DEFAULT_CACHE_DIR,
         spent: Callable[[], float] = spent_usd) -> int:
    try:
        args = _parse(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    try:
        answers = corruption_draw(load_answers(runs_dir))
        planned, skipped, skips = plan(answers, chunk_texts(answers, args.parsed_dir))
        committed = spent()
    except (AnswerFileError, DrawError, OSError, ValueError) as exc:
        print(f"could not start: {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    estimate = estimate_usd(planned)
    print(f"estimate: ${estimate:.4f} for {len(planned)} corrupted copies of {len(answers)} "
          f"answers ({sum(skipped.values())} skipped); Step 13 spent ${committed:.2f} of "
          f"${STEP_13_CAP_USD:.2f}")
    refused = _refusal(estimate, committed, args.max_cost)
    if refused is not None:
        print(refused, file=sys.stderr)
        return EXIT_USAGE
    problem = env_problem(JUDGES.required_env, JUDGES.forbidden_env, "eval.corrupt")
    if problem is not None:
        print(problem, file=sys.stderr)
        return EXIT_USAGE
    try:
        with open_judges(response_cache, metrics=METRICS) as judge:
            results, judged = judge_planned(judge, planned, response_cache)
    except JudgeRunError as exc:
        print(f"run stopped, no report written: {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    document = {
        "label": LABEL,
        "questions": "agent-drafted questions, dev",
        "commit": git_state(),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "seed": CORRUPTION_SEED,
        "excluded": f"every question of {DRAW_PATH.name}",
        "judging": JudgeConfig().as_header(),
        "metrics": list(METRICS),
        "estimate_usd": estimate,
        "spent_usd": fresh_usd(judged),
        "answers": [a.key for a in answers],
        "catch_rates": catch_rates(results, skipped),
        "skips": skips,
        "copies": [_copy_record(p, r) for p, r in zip(planned, results, strict=True)],
    }
    path = next_report_path(out_dir, RUN_PREFIX)
    write_json_atomic(path, document, indent=2)
    print(f"wrote {path} ({LABEL}); spent ${document['spent_usd']:.4f}")
    return EXIT_OK


def _copy_record(planned: Planned, result) -> dict[str, object]:
    return {
        "key": result.key, "kind": result.kind, "sentence_index": result.sentence_index,
        "before": planned.corruption.before, "after": planned.corruption.after,
        "original_faithfulness": list(result.original_faithfulness),
        "corrupted_faithfulness": list(result.corrupted_faithfulness),
        "original_verdicts": (None if result.original_verdicts is None
                              else [None if v is None else list(v)
                                    for v in result.original_verdicts]),
        "corrupted_verdicts": [None if v is None else list(v)
                               for v in result.corrupted_verdicts],
    }


if __name__ == "__main__":
    sys.exit(main())
