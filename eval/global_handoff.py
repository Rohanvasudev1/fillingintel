"""The graph_global chunk-handoff ablation: ``python -m eval.global_handoff`` (Step 13
ticket 02).

As built, graph_global's ranked list is the selected communities' shown chunks
by community rank, then cosine, so recall@10 sees the first two communities.
The ablation hands over the top 10 of the same shown chunks ranked by cosine
across all ``COMMUNITIES_N`` communities, each chunk once, ties by chunk ID.
Both are scored retrieval only, as the quality gate scores, on the answerable
``dev`` agent-drafted questions.  The arm is unchanged; the report is labelled
"ablation, dev".

Offline apart from the local databases: embeddings come from their disk caches
(``eval.global_n.open_cached_selection``).  The report goes to
``benchmarks/graph/global-handoff-run{N}.json``.

Exit codes: 0 done, 2 bad arguments, 3 could not run.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path

from eval.gate_scores import score_questions
from eval.global_n import QUESTIONS_LABEL, Selection, open_cached_selection
from eval.question_sets import EVAL_DIR, agent_drafted_set, load_question_sets
from eval.snapshot import gated_records
from graph.community import LOWER
from graph.load import OUTPUT_DIR as REPORT_DIR
from graph.load_report import next_report_path
from ingest.atomic_json import write_json_atomic
from ingest.provenance import git_state
from retrieve.arm import ArmError
from retrieve.community_select import CHUNKS_PER_COMMUNITY, ranked_list
from retrieve.global_retrieval import COMMUNITIES_N, ShownCommunity

EXIT_OK, EXIT_USAGE, EXIT_COULD_NOT_RUN = 0, 2, 3
LABEL = "ablation, dev"
HANDOFF_K = 10
REPORT_PREFIX = "global-handoff-run"
OpenSelection = Callable[[], AbstractContextManager[Selection]]


def handoff_ranking(communities: Sequence[ShownCommunity], k: int = HANDOFF_K) -> list[str]:
    """The top *k* shown chunk IDs by cosine across *communities*, each once, ties by ID."""
    best: dict[str, float] = {}
    for community in communities:
        for chunk in community.chunks:
            best[chunk.chunk_id] = max(chunk.score, best.get(chunk.chunk_id, chunk.score))
    return sorted(best, key=lambda c: (-best[c], c))[:k]


def main(argv: Sequence[str] | None = None, *, open_selection: OpenSelection | None = None,
         eval_dir: Path = EVAL_DIR, report_dir: Path = REPORT_DIR,
         commit: str | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score graph_global's top-10 chunk handoff on dev, retrieval only, offline.")
    try:
        parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    records = gated_records(agent_drafted_set(load_question_sets(eval_dir)).records)
    opener = open_selection or (lambda: open_cached_selection(LOWER))
    try:
        with opener() as selection:
            found = [(r, selection.retrieve(r.question).communities[:COMMUNITIES_N])
                     for r in records]
    except ArmError as exc:
        print(f"could not run: {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    rankings = {
        "as_built": [(r, [c.chunk_id for c in ranked_list(k.chunks for k in cs)])
                     for r, cs in found],
        "handoff": [(r, handoff_ranking(cs)) for r, cs in found],
    }
    scores = {name: {c: s.model_dump(by_alias=True)
                     for c, s in score_questions(ranked).items()}
              for name, ranked in rankings.items()}
    path = next_report_path(report_dir, REPORT_PREFIX)
    write_json_atomic(path, {
        "commit": commit if commit is not None else git_state(),
        "label": LABEL,
        "questions": QUESTIONS_LABEL,
        "n": len(records),
        "config": {"communities_n": COMMUNITIES_N, "chunks_per_community": CHUNKS_PER_COMMUNITY,
                   "handoff_k": HANDOFF_K, "level": LOWER},
        "rule": ("as built: shown chunks by community rank, then cosine; handoff: the top "
                 f"{HANDOFF_K} shown chunks by cosine across the communities, ties by chunk "
                 "ID; both scored as the quality gate scores"),
        "scores": scores,
    })
    for name, by_class in scores.items():
        print(name, {c: round(s["recall@10"], 3) for c, s in by_class.items()})
    print(f"Wrote {path}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
