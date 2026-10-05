"""Judge spot check: gpt-6-luna at two efforts against gpt-6-sol (Step 5, ticket 10).

Usage (needs the vector arm's keys and ``DATABASE_URL``, ``OPENAI_API_KEY`` with
``OPENAI_BASE_URL`` unset, and the network for anything not yet cached)::

    uv run --env-file .env python -m eval.spotcheck [--runs N]

It takes 10 dev questions (``eval.judging.spotcheck.select_questions``) and
answers them with the vector arm through the shared response cache, the same
calls ``eval.run`` makes, so any answer written here is the one the baseline
replays.  Each judge then scores those same answers ``--runs`` times (default 3,
as in a graded run, so the one-question decline and not-found rows get 3 verdicts).
The comparison against the reference and the judge it implies are printed and
written to ``benchmarks/runs/{date}-spotcheck-dev-{commit}.json``, which git
ignores.  The command never changes the judge config: that is a separate edit.

Every number it prints is labelled "spot check, not calibration" and stays
"uncalibrated" (agent-drafted questions).
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from eval.judging.openai_judge import JUDGE_RUNS
from eval.judging.report import question_judged, run_judge_usage
from eval.judging.runner import (
    JUDGES,
    JudgedQuestion,
    JudgeRunError,
    judge_all,
    open_judges,
)
from eval.judging.scoring import Judge, JudgeConfig, JudgeInput
from eval.judging.spotcheck import (
    LABEL,
    MAX_MEAN_GAP,
    MAX_MISSING_SHARE,
    MIN_AGREEMENT,
    QUOTA,
    MetricComparison,
    SpotCheckError,
    choose_judge,
    compare_judges,
    passes,
    select_questions,
)
from eval.question_sets import (
    EVAL_DIR,
    QuestionSet,
    QuestionSetError,
    agent_drafted_set,
    load_question_sets,
)
from eval.results import results_path, write_results
from eval.run import RUNS_DIR, env_problem
from eval.schema import EvalRecord
from ingest.provenance import git_state
from retrieve.arm import ArmError, ArmResult, ArmSpec
from retrieve.response_cache import DEFAULT_CACHE_DIR as DEFAULT_RESPONSE_CACHE
from retrieve.vector import VECTOR

# Cheapest first: the first that passes every metric judges the baseline.  Named here, not
# read from the judge config, because the spec fixes them and the config changes after the run.
CANDIDATES = (JudgeConfig(model="gpt-6-luna", effort="medium"),
              JudgeConfig(model="gpt-6-luna", effort="high"))
REFERENCE = JudgeConfig(model="gpt-6-sol", effort="medium")
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3

OpenJudges = Callable[[Path, JudgeConfig], AbstractContextManager[Judge]]
logger = logging.getLogger(__name__)


def judge_name(config: JudgeConfig) -> str:
    return f"{config.model}/{config.effort}"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare gpt-6-luna with gpt-6-sol as judges.")
    parser.add_argument("--runs", type=int, default=JUDGE_RUNS,
                        choices=range(1, JUDGE_RUNS + 1),
                        help=f"judge runs per question, 1 to {JUDGE_RUNS} (default {JUDGE_RUNS})")
    return parser.parse_args(argv)


def _answer(arm_spec: ArmSpec, records: Sequence[EvalRecord],
            cache_dir: Path) -> tuple[list[ArmResult], dict[str, Any]]:
    """The arm's result for each record, and what the header records about the answers."""
    with arm_spec.open(cache_dir) as arm:
        results = [arm.run(r.question) for r in records]
        config = arm.config
    answers = [r.answer for r in results]
    return results, {
        "arm": arm_spec.name,
        "models": dict(config.models),
        "efforts": dict(config.efforts),
        "answer_prompt": {"version": config.answer_prompt.version,
                          "sha256": config.answer_prompt.sha256},
        "replayed": sum(a.from_cache for a in answers),
        "new": sum(not a.from_cache for a in answers),
        "cost_usd": sum(a.cost_usd for a in answers if not a.from_cache),
    }


def _judge(open_judges_: OpenJudges, config: JudgeConfig, items: Sequence[JudgeInput],
           cache_dir: Path) -> list[JudgedQuestion]:
    logger.info("judging %d answers with %s, %d runs each", len(items), judge_name(config),
                config.runs)
    with open_judges_(cache_dir, config) as judge:
        return judge_all(judge, items)


def _comparison(config: JudgeConfig, comparisons: Sequence[MetricComparison]) -> dict[str, Any]:
    return {"judge": judge_name(config), "passed": passes(comparisons),
            "metrics": [asdict(c) for c in comparisons]}


def spot_check(arm_spec: ArmSpec, open_judges_: OpenJudges, records: Sequence[EvalRecord],
               cache_dir: Path, runs: int) -> dict[str, Any]:
    """Answer *records*, judge them with every candidate and the reference, and compare.

    Raises ``ArmError``, ``JudgeRunError`` or ``SpotCheckError``.
    """
    results, answers = _answer(arm_spec, records, cache_dir)
    items = [JudgeInput(r.question, r.class_, res.answer, res.sources)
             for r, res in zip(records, results, strict=True)]
    candidates = [replace(c, runs=runs) for c in CANDIDATES]
    reference = replace(REFERENCE, runs=runs)
    judged = {judge_name(c): dict(zip((r.id for r in records),
                                      _judge(open_judges_, c, items, cache_dir), strict=True))
              for c in (*candidates, reference)}
    by_reference = judged[judge_name(reference)]
    compared = [(c, compare_judges(judged[judge_name(c)], by_reference)) for c in candidates]
    chosen = choose_judge(compared, reference)
    return {
        "answers": answers,
        "judges": {judge_name(c): c.as_header() for c in (*candidates, reference)},
        "comparisons": [_comparison(c, m) for c, m in compared],
        "choice": judge_name(chosen),
        "judge_usage": {name: run_judge_usage(list(by_id.values()))
                        for name, by_id in judged.items()},
        "questions": [
            {"id": r.id, "class": r.class_,
             "judged": {name: question_judged(by_id[r.id], runs)
                        for name, by_id in judged.items()}}
            for r in records
        ],
    }


def _header(commit: str, created_at: str, question_set: QuestionSet,
            records: Sequence[EvalRecord], runs: int) -> dict[str, Any]:
    return {
        "label": LABEL,
        "question_set_label": question_set.label,
        "question_set_sha256": question_set.sha256,
        "judged_label": "uncalibrated",
        "created_at": created_at,
        "commit": commit,
        "split": "dev",
        "questions": [r.id for r in records],
        "quota": dict(QUOTA),
        "runs": runs,
        "candidates": [judge_name(c) for c in CANDIDATES],
        "reference": judge_name(REFERENCE),
        "thresholds": {"min_agreement": MIN_AGREEMENT, "max_mean_gap": MAX_MEAN_GAP,
                       "max_missing_share": MAX_MISSING_SHARE},
    }


def _format(document: Mapping[str, Any]) -> str:
    lines = [f"{document['header']['label']}; agent-drafted questions; judged scores "
             "uncalibrated",
             f"answers: {document['answers']['replayed']} replayed, "
             f"{document['answers']['new']} new (${document['answers']['cost_usd']:.4f})"]
    for result in document["comparisons"]:
        lines.append(f"{result['judge']} vs {document['header']['reference']}: "
                     f"{'PASS' if result['passed'] else 'FAIL'}")
        for m in result["metrics"]:
            value = "n/a" if m["value"] is None else f"{m['value']:.3f}"
            lines.append(f"  {m['metric']:<18} {m['kind']:<9} {value:>6}  compared "
                         f"{m['compared']:>3}  missing {m['missing']:>2}  "
                         f"{'pass' if m['passed'] else 'FAIL'}")
    for name, usage in document["judge_usage"].items():
        lines.append(f"{name}: {usage['calls']} calls, ${usage['cost_usd']:.4f}")
    lines.append(f"judge for the baseline: {document['choice']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None, *, arm_spec: ArmSpec = VECTOR,
         open_judges_: OpenJudges = open_judges, eval_dir: Path = EVAL_DIR,
         runs_dir: Path = RUNS_DIR, today: date | None = None,
         response_cache: Path = DEFAULT_RESPONSE_CACHE) -> int:
    """CLI entry point.  Exit codes: 0 ok; 2 bad input; 3 the check could not finish or be
    written (no results file)."""
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    problem = env_problem([*arm_spec.required_env, *JUDGES.required_env],
                          JUDGES.forbidden_env, "eval.spotcheck")
    if problem:
        print(problem, file=sys.stderr)
        return EXIT_USAGE
    try:
        question_set = agent_drafted_set(load_question_sets(eval_dir))
        records = select_questions(question_set.records)
    except (QuestionSetError, SpotCheckError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    commit = git_state()
    now = datetime.now(UTC)
    try:
        body = spot_check(arm_spec, open_judges_, records, response_cache, args.runs)
    except (ArmError, JudgeRunError, SpotCheckError) as exc:
        print(f"spot check stopped, no results written: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    document = {"header": _header(commit, now.isoformat(timespec="seconds"), question_set,
                                  records, args.runs), **body}
    try:
        path = results_path(runs_dir, today or now.date(), "spotcheck", "dev", commit)
        write_results(document, path)
    except OSError as exc:
        print(f"could not write results to {runs_dir}: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    print(_format(document))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
