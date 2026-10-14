"""Retrieval-only dev run of graph_traversal and its Cypher validity report:
``python -m eval.cypher_validity`` (Step 11 ticket 06).

Runs the arm's retrieval, with no answer model, on every ``dev`` agent-drafted
question (all classes: decline and unanswerable questions get Cypher too).  For
each class and pooled it reports four rates (CONTEXT.md, Cypher validity rate):

- first-pass valid: the first query passed the guard and ran without error in
  the timeout;
- valid after repair: the first query or its one repair did;
- non-empty: one of them returned chunks;
- fallback: the question fell back to graph_local.

Per question it keeps every attempt (query, outcome, rule, message, rows) and the
fallback reason, and for the answerable questions it scores retrieval as the
quality gate does.  Dev numbers are tuned on: at most one prompt revision
(``cypher/v2``) is allowed, and both versions' reports are kept.

Without ``--send`` it only prints the estimate for the Cypher calls the response
cache lacks; with ``--send`` it refuses above ``--max-cost``, then runs.  Query
and node embeddings come from their caches only.  The report goes to
``benchmarks/graph/cypher-validity-run{N}.json``.

Exit codes: 0 done, 2 bad arguments or over the cost cap, 3 could not run.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from eval.gate_scores import POOLED, score_questions
from eval.graph_report import header_graph
from eval.question_sets import EVAL_DIR, agent_drafted_set, load_question_sets
from eval.schema import NO_GOLD_CLASSES, Class, EvalRecord
from graph.load import OUTPUT_DIR as REPORT_DIR
from graph.load_report import next_report_path
from ingest.atomic_json import write_json_atomic
from ingest.provenance import git_state
from retrieve.answer_model import AnswerModel, AnswerModelError, AnthropicAnswerModel
from retrieve.arm import ArmConfig, ArmError, GraphRetrieval, Retrieval
from retrieve.cypher_estimate import estimate_usd
from retrieve.cypher_prompt import CURRENT_VERSION, PUBLISHED
from retrieve.cypher_request import CYPHER_EFFORT, CYPHER_MODEL, CypherRequest
from retrieve.graph_local import graph_connections
from retrieve.graph_traversal import GraphTraversalArm
from retrieve.pricing import PRICE_TABLE_DATE
from retrieve.query_cache import CachedBatchEmbedder, CachedQueryEmbedder, CacheOnly
from retrieve.response_cache import DEFAULT_CACHE_DIR, CachedAnswerModel

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_COULD_NOT_RUN = 3
QUESTIONS_LABEL = "agent-drafted questions, dev, all classes"
SCORED_LABEL = "agent-drafted questions, dev, answerable only"
REPORT_PREFIX = "cypher-validity-run"
DEFAULT_MAX_COST_USD = 5.0
ASSUMED_REPAIR_SHARE = 0.5  # the estimate assumes half the uncached questions need a repair
DEFAULT_WORKERS = 4
CLASS_ORDER: tuple[Class, ...] = ("lookup", "local", "multi_hop", "global", "decline",
                                  "unanswerable")
RATES = ("first_pass_valid", "valid_after_repair", "non_empty", "fallback")
_CYPHER_STAGES = ("cypher_generation", "cypher_repair")


class Traverser(Protocol):
    config: ArmConfig

    def retrieve(self, question: str) -> Retrieval: ...

    def cypher_requests(self, questions: Sequence[str]) -> tuple[CypherRequest, ...]: ...


OpenArm = Callable[[AnswerModel, str], AbstractContextManager[Traverser]]  # model, prompt version


class NoCalls:
    """The Cypher model for an estimate: every request must come from the cache."""

    def complete(self, request):
        raise AnswerModelError("a Cypher reply is not cached; rerun with --send")


def _flags(graph: GraphRetrieval) -> dict[str, bool]:
    return {"first_pass_valid": bool(graph.first_pass_valid),
            "valid_after_repair": bool(graph.valid_after_repair),
            "non_empty": bool(graph.non_empty),
            "fallback": graph.fallback_reason is not None}


def validity_rates(results: Sequence[tuple[EvalRecord, GraphRetrieval]]
                   ) -> dict[str, dict[str, float | int]]:
    """Per class present, then pooled over every question: n, and each rate's count and share."""
    groups: dict[str, list[GraphRetrieval]] = {}
    for cls in CLASS_ORDER:
        members = [g for r, g in results if r.class_ == cls]
        if members:
            groups[cls] = members
    groups[POOLED] = [g for _, g in results]
    rates: dict[str, dict[str, float | int]] = {}
    for name, graphs in groups.items():
        flags = [_flags(g) for g in graphs]
        row: dict[str, float | int] = {"n": len(graphs)}
        for rate in RATES:
            count = sum(f[rate] for f in flags)
            row[f"{rate}_count"] = count
            row[rate] = count / len(graphs) if graphs else 0.0
        rates[name] = row
    return rates


def _cost(graphs: Sequence[GraphRetrieval]) -> dict[str, object]:
    stages = [g.stages[s] for g in graphs for s in _CYPHER_STAGES if s in g.stages]
    return {"price_table": PRICE_TABLE_DATE, "calls": len(stages),
            "cached_calls": sum(s.from_cache for s in stages),
            "total_usd": sum(s.cost_usd for s in stages),
            "new_spend_usd": sum(s.cost_usd for s in stages if not s.from_cache),
            "input_tokens": sum(s.input_tokens for s in stages),
            "output_tokens": sum(s.output_tokens for s in stages)}


def _question(record: EvalRecord, retrieval: Retrieval) -> dict[str, object]:
    graph = retrieval.graph
    return {"id": record.id, "class": record.class_, "question": record.question,
            "fallback_reason": graph.fallback_reason, "validity": _flags(graph),
            "attempts": [asdict(a) for a in graph.cypher_attempts],
            "retrieved": [c.chunk_id for c in retrieval.retrieved]}


def _print(rates: Mapping[str, Mapping[str, float | int]], cost: Mapping[str, object]) -> None:
    print(f"graph_traversal Cypher validity, {QUESTIONS_LABEL}")
    print("| class | n | first-pass valid | valid after repair | non-empty | fallback |")
    print("|---|---|---|---|---|---|")
    for name, row in rates.items():
        cells = " | ".join(f"{row[r]:.3f} ({row[f'{r}_count']})" for r in RATES)
        print(f"| {name} | {row['n']} | {cells} |")
    print(f"Cypher calls: {cost['calls']} ({cost['cached_calls']} cached), "
          f"${cost['total_usd']:.4f} in all, ${cost['new_spend_usd']:.4f} new")


def main(argv: Sequence[str] | None = None, *, open_arm: OpenArm | None = None,
         claude: Callable[[], AbstractContextManager[AnswerModel]] = AnthropicAnswerModel.from_env,
         eval_dir: Path = EVAL_DIR, report_dir: Path = REPORT_DIR,
         cache_dir: Path = DEFAULT_CACHE_DIR, commit: str | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Retrieval-only dev run of graph_traversal and its Cypher validity report.")
    parser.add_argument("--send", action="store_true",
                        help="make the Cypher calls the cache lacks (default: estimate only)")
    parser.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST_USD,
                        help="refuse when the estimate exceeds this many USD")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--prompt", default=CURRENT_VERSION, choices=sorted(PUBLISHED),
                        help="the Cypher prompt version")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    records = tuple(r for r in agent_drafted_set(load_question_sets(eval_dir)).records
                    if r.split == "dev")
    opener = open_arm if open_arm is not None else open_cached_arm
    try:
        with (claude() if args.send else _no_calls()) as inner:
            cypher_model = CachedAnswerModel(inner, cache_dir)
            with opener(cypher_model, args.prompt) as arm:
                pending = [r for r in arm.cypher_requests([r.question for r in records])
                           if not cypher_model.contains(r)]
                estimate = estimate_usd(pending, repair_share=ASSUMED_REPAIR_SHARE)
                print(f"{len(records)} questions, {arm.config.graph.cypher_prompt_version}: "
                      f"{len(pending)} first calls not cached; estimate ${estimate:.2f} "
                      f"(assuming {ASSUMED_REPAIR_SHARE:.0%} need a repair)")
                if not args.send:
                    return EXIT_OK
                if estimate > args.max_cost:
                    print(f"refused: the estimate is over --max-cost ${args.max_cost:.2f}",
                          file=sys.stderr)
                    return EXIT_USAGE
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    retrievals = list(pool.map(arm.retrieve, [r.question for r in records]))
                config = arm.config
    except (ArmError, AnswerModelError) as exc:
        print(f"could not run: {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    path = _write(records, retrievals, config, report_dir, commit)
    print(f"Wrote {path}")
    return EXIT_OK


def _write(records: Sequence[EvalRecord], retrievals: Sequence[Retrieval], config: ArmConfig,
           report_dir: Path, commit: str | None) -> Path:
    graphs = [r.graph for r in retrievals]
    rates = validity_rates(list(zip(records, graphs, strict=True)))
    cost = _cost(graphs)
    _print(rates, cost)
    scored = [(rec, [c.chunk_id for c in ret.retrieved])
              for rec, ret in zip(records, retrievals, strict=True)
              if rec.class_ not in NO_GOLD_CLASSES]
    path = next_report_path(report_dir, REPORT_PREFIX)
    write_json_atomic(path, {
        "commit": commit if commit is not None else git_state(),
        "questions": QUESTIONS_LABEL,
        "n": len(records),
        "prompt": {"version": config.graph.cypher_prompt_version,
                   "sha256": config.graph.cypher_prompt_sha256},
        "model": CYPHER_MODEL,
        "effort": CYPHER_EFFORT,
        "graph": header_graph(config.graph),
        "rates": rates,
        "retrieval": {"questions": SCORED_LABEL, "k": config.k,
                      "scores": {c: s.model_dump(by_alias=True)
                                 for c, s in score_questions(scored).items()}},
        "cost": cost,
        "questions_detail": [_question(rec, ret)
                             for rec, ret in zip(records, retrievals, strict=True)],
    })
    return path


@contextmanager
def _no_calls() -> Iterator[AnswerModel]:
    yield NoCalls()


@contextmanager
def open_cached_arm(cypher_model: AnswerModel, prompt_version: str
                    ) -> Iterator[GraphTraversalArm]:
    """graph_traversal on ``DATABASE_URL`` and ``NEO4J_*``, embeddings from cache only."""
    hint = "this run is cache-only (python -m retrieve.node_embed embeds nodes)"
    with graph_connections() as (conn, driver):
        yield GraphTraversalArm(conn, driver, CachedQueryEmbedder(CacheOnly(hint)),
                                CachedBatchEmbedder(CacheOnly(hint)), cypher_model, None,
                                cypher_prompt_version=prompt_version)


if __name__ == "__main__":
    raise SystemExit(main())
