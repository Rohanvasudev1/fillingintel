"""Retrieval-only dev scores for graph_local at 1 and 2 hops: ``python -m eval.local_hops``
(Step 11 ticket 05).

Runs the arm's retrieval, with no answer model, on the answerable ``dev``
agent-drafted questions (the quality gate's set), scores recall@5, recall@10
and the retrieval wrong-evidence rate as the gate does, and counts no-anchor
questions.  The higher pooled recall@10 is the choice; a tie goes to 1 hop.
The choice is frozen by hand in ``retrieve.graph_local.HOPS``; this command
only reports it.  Dev numbers chosen this way are tuned on.

Offline apart from the local databases: query embeddings and node embeddings
come from their disk caches, and a missing one stops the run.  The report goes
to ``benchmarks/graph/local-hops-run{N}.json``.

Exit codes: 0 done, 2 bad arguments, 3 could not run.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Protocol

from eval.gate_scores import GATED_CLASSES, POOLED, ClassScores, score_questions
from eval.question_sets import EVAL_DIR, agent_drafted_set, load_question_sets
from eval.schema import EvalRecord
from eval.snapshot import gated_records
from graph.load import OUTPUT_DIR as REPORT_DIR
from graph.load_report import next_report_path
from ingest.atomic_json import write_json_atomic
from ingest.provenance import git_state
from ingest.voyage import DEFAULT_MODEL
from retrieve.anchors import ANCHOR_LIMIT, NAME_MATCH_CUTOFF
from retrieve.arm import ArmError, Retrieval
from retrieve.graph_expand import HOP_CHOICES
from retrieve.graph_local import TOP_K, GraphLocalArm, graph_connections
from retrieve.query_cache import CachedBatchEmbedder, CachedQueryEmbedder, CacheOnly

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_COULD_NOT_RUN = 3
QUESTIONS_LABEL = "agent-drafted questions, dev, answerable only"
REPORT_PREFIX = "local-hops-run"


class Retriever(Protocol):
    def retrieve(self, question: str) -> Retrieval: ...


OpenArm = Callable[[int], AbstractContextManager[Retriever]]


def choose_hops(scores: Mapping[int, Mapping[str, ClassScores]]) -> int:
    """The hop count with the higher pooled recall@10; a tie goes to the fewer hops."""
    return max(sorted(scores), key=lambda hops: (scores[hops][POOLED].recall_at_10, -hops))


def _score(arm: Retriever, records: Sequence[EvalRecord]
           ) -> tuple[dict[str, ClassScores], dict[str, int]]:
    scored, no_anchor = [], Counter()
    for record in records:
        retrieval = arm.retrieve(record.question)
        scored.append((record, [c.chunk_id for c in retrieval.retrieved]))
        if retrieval.graph.no_anchor:
            no_anchor[record.class_] += 1
            no_anchor[POOLED] += 1
    counts = {cls: no_anchor[cls] for cls in (*GATED_CLASSES, POOLED)
              if cls == POOLED or any(r.class_ == cls for r in records)}
    return score_questions(scored), counts


def _print(scores: Mapping[int, Mapping[str, ClassScores]], chosen: int) -> None:
    print(f"graph_local retrieval only, {QUESTIONS_LABEL}")
    print("| hops | class | n | recall@5 | recall@10 | wrong evidence |")
    print("|---|---|---|---|---|---|")
    for hops, by_class in scores.items():
        for cls, s in by_class.items():
            wrong = "none" if s.wrong_evidence is None else f"{s.wrong_evidence:.3f}"
            print(f"| {hops} | {cls} | {s.n} | {s.recall_at_5:.3f} | {s.recall_at_10:.3f} "
                  f"| {wrong} |")
    print(f"Chosen: {chosen} hop(s) (higher pooled recall@10; a tie goes to 1).")


def main(argv: Sequence[str] | None = None, *, open_arm: OpenArm | None = None,
         eval_dir: Path = EVAL_DIR, report_dir: Path = REPORT_DIR,
         commit: str | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score graph_local retrieval on dev at 1 and 2 hops, offline.")
    try:
        parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    records = gated_records(agent_drafted_set(load_question_sets(eval_dir)).records)
    opener = open_arm if open_arm is not None else open_cached_arm
    scores, no_anchor = {}, {}
    try:
        for hops in HOP_CHOICES:
            with opener(hops) as arm:
                scores[hops], no_anchor[hops] = _score(arm, records)
    except ArmError as exc:
        print(f"could not run: {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    chosen = choose_hops(scores)
    _print(scores, chosen)
    path = next_report_path(report_dir, REPORT_PREFIX)
    write_json_atomic(path, {
        "commit": commit if commit is not None else git_state(),
        "questions": QUESTIONS_LABEL,
        "n": len(records),
        "config": {"anchors": ANCHOR_LIMIT, "name_match_cutoff": NAME_MATCH_CUTOFF,
                   "k": TOP_K, "embedding_model": DEFAULT_MODEL},
        "rule": "higher pooled recall@10; a tie goes to 1 hop",
        "chosen_hops": chosen,
        "scores": {str(h): {c: s.model_dump(by_alias=True) for c, s in by_class.items()}
                   for h, by_class in scores.items()},
        "no_anchor": {str(h): counts for h, counts in no_anchor.items()},
    })
    print(f"Wrote {path}")
    return EXIT_OK


@contextmanager
def open_cached_arm(hops: int) -> Iterator[GraphLocalArm]:
    """graph_local on ``DATABASE_URL`` and ``NEO4J_*``, reading embeddings from cache only."""
    hint = "this run is cache-only (python -m retrieve.node_embed embeds nodes)"
    with graph_connections() as (conn, driver):
        yield GraphLocalArm(conn, driver, CachedQueryEmbedder(CacheOnly(hint)),
                            CachedBatchEmbedder(CacheOnly(hint)), None, hops=hops)


if __name__ == "__main__":
    raise SystemExit(main())
