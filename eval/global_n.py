"""Retrieval-only dev scores for graph_global at each N: ``python -m eval.global_n``
(Step 12 ticket 05).

Runs graph_global's retrieval (``retrieve.global_retrieval``), with no model
call, on the answerable ``dev`` agent-drafted questions (the quality gate's
set).  Each question is retrieved once at the largest N; a smaller N takes the
first N selected communities, which is exactly what selecting N would give.
Each N in ``N_CHOICES`` is scored as the gate scores (recall@5, recall@10, the
retrieval wrong-evidence rate), per class and pooled.  N is chosen by the 10
global questions' recall@10, a tie going to the smaller N; the choice is frozen
by hand in ``retrieve.global_retrieval.COMMUNITIES_N``, and this command only
reports it.  Dev numbers chosen this way are tuned on: the report says so.

The same scores over top-level communities are the ablation.  Beside them the
report gives, per N, the mean number of chunks shown and recall over all of
them, since map calls see every shown chunk and recall@10 sees the first 10.

Offline apart from the local databases: query and report embeddings come from
their disk caches, and a missing one stops the run.  The report goes to
``benchmarks/graph/global-n-run{N}.json``.

Exit codes: 0 done, 2 bad arguments, 3 could not run.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from statistics import fmean
from typing import Protocol

from eval.gate_scores import DECIMALS, GATED_CLASSES, POOLED, ClassScores, score_questions
from eval.metrics import recall_at_k
from eval.question_sets import EVAL_DIR, agent_drafted_set, load_question_sets
from eval.schema import EvalRecord
from eval.snapshot import gated_records
from graph.community import LOWER, TOP, Level
from graph.load import OUTPUT_DIR as REPORT_DIR
from graph.load_report import next_report_path
from ingest.atomic_json import write_json_atomic
from ingest.provenance import git_state
from ingest.voyage import DEFAULT_MODEL
from retrieve.arm import ArmError
from retrieve.community_select import CHUNKS_PER_COMMUNITY
from retrieve.global_retrieval import (
    N_CHOICES,
    GlobalRetrieval,
    GlobalSelection,
    loaded_reports_file,
)
from retrieve.graph_local import graph_connections
from retrieve.query_cache import CachedBatchEmbedder, CachedQueryEmbedder, CacheOnly

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_COULD_NOT_RUN = 3
QUESTIONS_LABEL = "agent-drafted questions, dev, answerable only"
TUNED_LABEL = "tuned on dev, n = 10"
TIE_NOTE = ("uninformative on recall@10: global recall@10 is the same at every N, so the tie "
            "rule chose the smallest N; the shown-chunk diagnostics are not used to choose")
CHOICE_CLASS = "global"
REPORT_PREFIX = "global-n-run"
LEVELS: tuple[Level, ...] = (LOWER, TOP)


class Selection(Protocol):
    def retrieve(self, question: str) -> GlobalRetrieval: ...


OpenSelection = Callable[[Level], AbstractContextManager[Selection]]
Retrieved = Sequence[tuple[EvalRecord, GlobalRetrieval]]


def choose_n(scores: Mapping[int, Mapping[str, ClassScores]]) -> int:
    """The N with the highest global recall@10; a tie goes to the smaller N."""
    return max(sorted(scores), key=lambda n: (scores[n][CHOICE_CLASS].recall_at_10, -n))


def ties_at_every_n(scores: Mapping[int, Mapping[str, ClassScores]]) -> bool:
    """Whether global recall@10 is the same at every N, so only the tie rule chose."""
    return len({round(s[CHOICE_CLASS].recall_at_10, DECIMALS) for s in scores.values()}) == 1


def _classes(records: Sequence[EvalRecord]) -> list[str]:
    return [c for c in GATED_CLASSES if any(r.class_ == c for r in records)] + [POOLED]


def _in_class(cls: str, record: EvalRecord) -> bool:
    return cls == POOLED or record.class_ == cls


def _scores(found: Retrieved, n: int) -> dict[str, ClassScores]:
    return score_questions([(record, got.ranked_ids(n)) for record, got in found])


def _shown(found: Retrieved, n: int, classes: Sequence[str]) -> dict[str, dict[str, float]]:
    """Per class, the mean number of chunks shown and the mean recall over all of them."""
    out = {}
    for cls in classes:
        rows = [_shown_recall(got.ranked_ids(n), r) for r, got in found if _in_class(cls, r)]
        out[cls] = {"mean_shown_chunks": fmean(size for size, _ in rows),
                    "recall_shown": fmean(recall for _, recall in rows)}
    return out


def _shown_recall(ids: Sequence[str], record: EvalRecord) -> tuple[int, float]:
    """How many chunks were shown, and the share of gold among them."""
    return len(ids), recall_at_k(ids, record.gold_chunk_ids, len(ids)) if ids else 0.0


def _empty_counts(found: Retrieved, classes: Sequence[str]) -> dict[str, int]:
    counts = Counter(cls for r, got in found if not got.communities
                     for cls in classes if _in_class(cls, r))
    return {cls: counts[cls] for cls in classes}


def _retrieve_all(open_selection: OpenSelection, records: Sequence[EvalRecord]
                  ) -> dict[Level, Retrieved]:
    found = {}
    for level in LEVELS:
        with open_selection(level) as selection:
            found[level] = [(r, selection.retrieve(r.question)) for r in records]
    return found


def _print(scores: Mapping[Level, Mapping[int, Mapping[str, ClassScores]]],
           shown: Mapping[Level, Mapping[int, Mapping[str, Mapping[str, float]]]],
           chosen: int) -> None:
    print(f"graph_global retrieval only, {QUESTIONS_LABEL}, {TUNED_LABEL}")
    print("| level | N | class | n | recall@5 | recall@10 | wrong evidence | shown | "
          "recall shown |")
    print("|---|---|---|---|---|---|---|---|---|")
    for level, by_n in scores.items():
        for n, by_class in by_n.items():
            for cls, s in by_class.items():
                wrong = "none" if s.wrong_evidence is None else f"{s.wrong_evidence:.3f}"
                extra = shown[level][n][cls]
                print(f"| {level} | {n} | {cls} | {s.n} | {s.recall_at_5:.3f} | "
                      f"{s.recall_at_10:.3f} | {wrong} | {extra['mean_shown_chunks']:.1f} | "
                      f"{extra['recall_shown']:.3f} |")
    print(f"Chosen: N = {chosen} (highest lower-level global recall@10; a tie goes to the "
          "smaller N).")


def main(argv: Sequence[str] | None = None, *, open_selection: OpenSelection | None = None,
         eval_dir: Path = EVAL_DIR, report_dir: Path = REPORT_DIR,
         commit: str | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score graph_global retrieval on dev at each N, offline.")
    try:
        parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    records = gated_records(agent_drafted_set(load_question_sets(eval_dir)).records)
    try:
        found = _retrieve_all(open_selection or open_cached_selection, records)
    except ArmError as exc:
        print(f"could not run: {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    classes = _classes(records)
    scores = {level: {n: _scores(found[level], n) for n in N_CHOICES} for level in LEVELS}
    shown = {level: {n: _shown(found[level], n, classes) for n in N_CHOICES}
             for level in LEVELS}
    chosen = choose_n(scores[LOWER])
    tied = ties_at_every_n(scores[LOWER])
    _print(scores, shown, chosen)
    if tied:
        print(f"Note: {TIE_NOTE}.")
    path = next_report_path(report_dir, REPORT_PREFIX)
    write_json_atomic(path, {
        "commit": commit if commit is not None else git_state(),
        "questions": QUESTIONS_LABEL,
        "label": TUNED_LABEL,
        "n": len(records),
        "config": {"n_choices": list(N_CHOICES), "chunks_per_community": CHUNKS_PER_COMMUNITY,
                   "embedding_model": DEFAULT_MODEL, "selected_level": LOWER,
                   "ablation_level": TOP},
        "rule": "highest lower-level global recall@10; a tie goes to the smaller N",
        "chosen_n": chosen,
        "recall_at_10_ties_at_every_n": tied,
        "choice_note": TIE_NOTE if tied else None,
        "scores": {level: {str(n): {c: s.model_dump(by_alias=True) for c, s in by_class.items()}
                           for n, by_class in by_n.items()}
                   for level, by_n in scores.items()},
        "shown_note": "diagnostics only, not used to choose N: mean chunks shown to map "
                      "calls and recall over all of them",
        "shown": {level: {str(n): by_class for n, by_class in by_n.items()}
                  for level, by_n in shown.items()},
        "empty": {level: _empty_counts(found[level], classes) for level in LEVELS},
    })
    print(f"Wrote {path}")
    return EXIT_OK


@contextmanager
def open_cached_selection(level: Level) -> Iterator[GlobalSelection]:
    """graph_global's retrieval at the largest N, reading embeddings from cache only."""
    hint = "this run is cache-only (python -m retrieve.node_embed embeds report texts)"
    reports = loaded_reports_file()
    with graph_connections() as (conn, driver):
        yield GlobalSelection(conn, driver, CachedQueryEmbedder(CacheOnly(hint)),
                              CachedBatchEmbedder(CacheOnly(hint)), reports,
                              n=max(N_CHOICES), level=level)


if __name__ == "__main__":
    raise SystemExit(main())
