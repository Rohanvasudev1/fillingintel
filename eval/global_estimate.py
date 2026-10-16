"""The full graph_global run's estimate against Step 12's cap: ``python -m eval.global_estimate
--classes global,decline,unanswerable`` (Step 12 ticket 07).

Run it before ``eval.run --arm graph_global``.  It selects each ``dev`` question's
communities as the arm does, from cached embeddings, builds every map request and
prices them with ``retrieve.global_estimate`` (meant to run high, one reduce call
per question with a community).  Judges are priced from a past run that judged
the same questions (``graph_local``'s dev run), times ``JUDGE_MARGIN``; a question
it did not judge is priced at its highest per-question cost.  It sends nothing.

Exit codes: 0 the estimate fits Step 12's remaining budget; 2 bad arguments;
3 could not run; 4 the estimate would pass the cap, so the run must not start.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from eval.question_sets import EVAL_DIR, agent_drafted_set, load_question_sets
from eval.run import ClassSelectionError, parse_classes
from eval.schema import EvalRecord
from graph.community import LOWER
from graph.step12_spend import COMMUNITIES_DIR, RUNS_DIR, STEP_12_CAP_USD, spent_usd
from retrieve.arm import ArmError
from retrieve.arm_steps import filing_tickers, source_chunks
from retrieve.global_estimate import estimate_usd
from retrieve.global_map import build_map_request
from retrieve.global_prompts import load_map_prompt, load_reduce_prompt
from retrieve.global_retrieval import COMMUNITIES_N, GlobalSelection, loaded_reports_file
from retrieve.graph_local import graph_connections
from retrieve.query_cache import CachedBatchEmbedder, CachedQueryEmbedder, CacheOnly

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3
EXIT_OVER_CAP = 4
JUDGE_MARGIN = 2.0
SPLIT = "dev"
JUDGED_RUN = RUNS_DIR / "2026-10-14-graph_local-dev-375efac.json"

ModelEstimate = Callable[[Sequence[EvalRecord]], float]


def cached_model_estimate(records: Sequence[EvalRecord]) -> float:
    """Map and reduce calls for *records*, selected as the arm selects, from cached vectors."""
    hint = "this estimate is cache-only (python -m retrieve.node_embed embeds report texts)"
    reports = loaded_reports_file()
    map_prompt = load_map_prompt()
    requests, reduce_calls = [], 0
    with graph_connections() as (conn, driver):
        selection = GlobalSelection(conn, driver, CachedQueryEmbedder(CacheOnly(hint)),
                                    CachedBatchEmbedder(CacheOnly(hint)), reports,
                                    n=COMMUNITIES_N, level=LOWER)
        tickers = filing_tickers(selection.filings)
        for record in records:
            found = selection.retrieve(record.question)
            reduce_calls += bool(found.communities)
            for community in found.communities:
                entry = reports.entries[community.community_id]
                chunks = source_chunks(conn, tickers, [c.chunk_id for c in community.chunks])
                requests.append(build_map_request(map_prompt, record.question, entry.title,
                                                  entry.report, chunks))
    return estimate_usd(requests, load_reduce_prompt(), reduce_calls)


def judge_estimate(records: Sequence[EvalRecord], judged_run: Path) -> float:
    """The past run's judge cost for *records*, times ``JUDGE_MARGIN``; ValueError if none."""
    try:
        run = json.loads(judged_run.read_text(encoding="utf-8"))
        costs = {q["id"]: q["judged"]["cost_usd"] for q in run["questions"]
                 if q.get("judged") is not None}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot read {judged_run.name}: {type(exc).__name__}: {exc}") from exc
    wanted = {r.id for r in records}
    known = [c for q, c in costs.items() if q in wanted]
    if not known:
        raise ValueError(f"{judged_run.name} judged none of the {len(wanted)} questions")
    highest = max(known)
    return JUDGE_MARGIN * sum(costs.get(r.id, highest) for r in records)


def _records(classes: Sequence[str], eval_dir: Path) -> list[EvalRecord]:
    records = agent_drafted_set(load_question_sets(eval_dir)).records
    return [r for r in records if r.split == SPLIT and r.class_ in classes]


def main(argv: Sequence[str] | None = None, *, model_estimate: ModelEstimate | None = None,
         eval_dir: Path = EVAL_DIR, communities_dir: Path = COMMUNITIES_DIR,
         runs_dir: Path = RUNS_DIR, judged_run: Path = JUDGED_RUN) -> int:
    parser = argparse.ArgumentParser(
        description="Estimate a graph_global dev run and check it against Step 12's cap.")
    parser.add_argument("--classes", required=True)
    try:
        args = parser.parse_args(argv)
        classes = parse_classes(args.classes)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    except ClassSelectionError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE
    records = _records(classes, eval_dir)
    try:
        models = (model_estimate or cached_model_estimate)(records)
        judges = judge_estimate(records, judged_run)
        spent = spent_usd(communities_dir, runs_dir)
    except (ArmError, ValueError, OSError) as exc:
        print(f"could not estimate: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    total, remaining = models + judges, STEP_12_CAP_USD - spent
    print(f"graph_global on {len(records)} {SPLIT} questions ({', '.join(classes)}): estimate "
          f"${total:.2f} (map and reduce ${models:.2f}, judges ${judges:.2f}). Step 12 spend "
          f"so far ${spent:.2f} of ${STEP_12_CAP_USD:.2f}, remaining ${remaining:.2f}.")
    if total > remaining:
        print(f"the estimate ${total:.2f} would pass the ${STEP_12_CAP_USD:.2f} cap; do not "
              "start the run", file=sys.stderr)
        return EXIT_OVER_CAP
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
