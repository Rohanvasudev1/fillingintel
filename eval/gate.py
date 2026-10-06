"""The quality gate: the vector arm's real search, offline, from the snapshot (Step 6, ADR-0003).

Usage (needs only ``DATABASE_URL``; no API key, no network)::

    uv run --env-file .env python -m eval.gate
    uv run --env-file .env python -m eval.gate --update-baseline

The gate loads the committed snapshot into a throwaway schema on
``DATABASE_URL``, so it never reads or changes the tables already there, and
drops the schema afterwards.  It runs the vector arm's ``retrieve`` on the
answerable ``dev`` agent-drafted questions with the snapshot's stored query
vectors, scores recall@5, recall@10 and the retrieval wrong-evidence rate per
class and pooled, and compares them with ``benchmarks/gate_baseline.json``.
There is no option to select the ``test`` split.

``--update-baseline`` rewrites the baseline from the current scores and prints
what changed.  Agents never lower it to get a change through (invariant 5).
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

import psycopg
from psycopg import sql

from eval.gate_baseline import (
    BASELINE_PATH,
    BaselineError,
    GateBaseline,
    baseline_changes,
    baseline_mismatches,
    read_baseline,
    write_baseline,
)
from eval.gate_scores import (
    GATED_METRICS,
    ClassScores,
    Drop,
    compare,
    question_set_sha256,
    score_questions,
)
from eval.question_sets import (
    AGENT_DRAFTED_LABEL,
    EVAL_DIR,
    QuestionSetError,
    agent_drafted_set,
    load_question_sets,
)
from eval.schema import EvalRecord
from eval.snapshot import (
    GATED_SPLIT,
    SNAPSHOT_PATH,
    Snapshot,
    SnapshotError,
    SnapshotQueryEmbedder,
    gated_records,
    load_snapshot,
    read_snapshot,
)
from ingest.provenance import git_state
from ingest.voyage import DEFAULT_MODEL
from retrieve.arm import ArmError
from retrieve.vector import TOP_K, VectorArm

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3
EXIT_DROPPED = 4
SUMMARY_ENV = "GITHUB_STEP_SUMMARY"
_SHOWN = 6

OpenDatabase = Callable[[Path], AbstractContextManager[psycopg.Connection]]


class GateUsageError(ValueError):
    """The gate cannot start with these inputs; the message is safe to print."""


@contextmanager
def snapshot_database(snapshot_path: Path) -> Iterator[psycopg.Connection]:
    """A connection to ``DATABASE_URL`` whose search path starts with a new schema holding
    the snapshot's rows.  The schema is dropped on exit.  Raises ``GateUsageError``
    without ``DATABASE_URL``, ``SnapshotError`` if the snapshot does not load, and
    ``psycopg.Error`` if the database is unreachable."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise GateUsageError("DATABASE_URL not set (run with: uv run --env-file .env "
                             "python -m eval.gate)")
    schema = sql.Identifier(f"gate_{uuid.uuid4().hex[:12]}")
    with psycopg.connect(url) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
        # public stays on the path: pgvector's type lives there.
        conn.execute(sql.SQL("SET search_path TO {}, public").format(schema))
        conn.commit()
        try:
            load_snapshot(conn, snapshot_path)
            yield conn
        finally:
            conn.rollback()
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(schema))
            conn.commit()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score the vector arm's retrieval offline and compare with the gate baseline."
    )
    parser.add_argument("--update-baseline", action="store_true",
                        help="rewrite the gate baseline from the current scores")
    return parser.parse_args(argv)


def _retrieve_all(
    conn: psycopg.Connection, snapshot: Snapshot, records: Sequence[EvalRecord], k: int
) -> list[tuple[EvalRecord, list[str]]]:
    """Each record with the chunk IDs the vector arm retrieves for its question, best first."""
    arm = VectorArm(conn, SnapshotQueryEmbedder(snapshot.query_vectors), None,
                    model=DEFAULT_MODEL, k=k)
    return [(r, [c.chunk_id for c in arm.retrieve(r.question).retrieved]) for r in records]


# ── Output ────────────────────────────────────────────────────────────────────

def _fmt(value: float | None, sign: str = "") -> str:
    return "–" if value is None else f"{value:{sign}.{_SHOWN}f}"


def _rows(baseline: dict[str, ClassScores] | None, current: dict[str, ClassScores],
          drops: Sequence[Drop]) -> list[str]:
    dropped = {(d.class_, d.metric) for d in drops}
    rows = []
    for cls, scores in current.items():
        old_scores = (baseline or {}).get(cls)
        for metric in GATED_METRICS:
            new = scores.metric(metric)
            old = None if old_scores is None else old_scores.metric(metric)
            delta = None if old is None or new is None else new - old
            result = "DROPPED" if (cls, metric) in dropped else "ok"
            rows.append(f"| {cls} | {scores.n} | {metric} | {_fmt(old)} | {_fmt(new)} "
                        f"| {_fmt(delta, '+')} | {result} |")
    return rows


def _report(baseline: dict[str, ClassScores] | None, current: dict[str, ClassScores],
            drops: Sequence[Drop], k: int, verdict: str) -> str:
    n = current["pooled"].n
    return "\n".join([
        f"## Quality gate: vector arm, {n} answerable {GATED_SPLIT} questions "
        f"({AGENT_DRAFTED_LABEL}), {DEFAULT_MODEL}, k={k}",
        "",
        "| class | n | metric | baseline | current | delta | result |",
        "|---|---|---|---|---|---|---|",
        *_rows(baseline, current, drops),
        "",
        verdict,
        "",
    ])


def _drop_line(d: Drop) -> str:
    return (f"DROPPED {d.class_} {d.metric}: baseline {_fmt(d.baseline)}, "
            f"current {_fmt(d.current)}, delta {_fmt(d.delta, '+')}")


def _publish(report: str) -> None:
    """Print *report*, and append it to the GitHub job summary when that is set."""
    print(report)
    summary = os.environ.get(SUMMARY_ENV)
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")


# ── Modes ─────────────────────────────────────────────────────────────────────

def _update(path: Path, new: GateBaseline, k: int) -> int:
    try:
        old = read_baseline(path)
    except BaselineError:
        old = None
    write_baseline(path, new)
    changes = baseline_changes(old, new)
    lowered = any("LOWERED" in line for line in changes)
    _publish(_report(old.scores if old else None, new.scores, (), k,
                     f"Gate baseline written to {path}."))
    print("Changes from the previous gate baseline:")
    for line in changes or ["none"]:
        print(f"  {line}")
    if lowered:
        print("This lowers the gate baseline: that needs the user's yes in chat and a "
              "BUILD-LOG line (invariant 5).")
    return EXIT_OK


def _check(baseline: GateBaseline, current: dict[str, ClassScores], k: int) -> int:
    drops = compare(baseline.scores, current)
    verdict = (f"FAIL: {len(drops)} gated numbers dropped." if drops
               else "PASS: no gated number dropped.")
    _publish(_report(baseline.scores, current, drops, k, verdict))
    for d in drops:
        print(_drop_line(d))
    return EXIT_DROPPED if drops else EXIT_OK


class _Stop(Exception):
    """The gate stops early with *code*; the message is safe to print."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _inputs(eval_dir: Path, snapshot_path: Path) -> tuple[tuple[EvalRecord, ...], Snapshot]:
    """The gated records and the checked snapshot."""
    try:
        records = gated_records(agent_drafted_set(load_question_sets(eval_dir)).records)
    except QuestionSetError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    try:
        snapshot = read_snapshot(snapshot_path)
    except SnapshotError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"the gate could not run: {exc}") from exc
    if snapshot.model != DEFAULT_MODEL:
        raise _Stop(EXIT_USAGE, f"the snapshot holds {snapshot.model} vectors; "
                                f"the vector arm uses {DEFAULT_MODEL}")
    return records, snapshot


def _matching_baseline(path: Path, snapshot: Snapshot, question_sha: str, k: int) -> GateBaseline:
    """The baseline at *path*, measured against the same inputs as this run."""
    try:
        baseline = read_baseline(path)
    except BaselineError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    problems = baseline_mismatches(baseline, snapshot_sha256=snapshot.sha256,
                                   question_set_sha256=question_sha,
                                   embedding_model=DEFAULT_MODEL, k=k)
    if problems:
        raise _Stop(EXIT_USAGE, "\n  ".join(
            ["the gate's inputs differ from the gate baseline's:", *problems]))
    return baseline


def _score(open_database: OpenDatabase, snapshot_path: Path, snapshot: Snapshot,
           records: Sequence[EvalRecord], k: int) -> dict[str, ClassScores]:
    """The current scores, from the vector arm's search over the loaded snapshot."""
    try:
        with open_database(snapshot_path) as conn:
            return score_questions(_retrieve_all(conn, snapshot, records, k))
    except GateUsageError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except (ArmError, SnapshotError) as exc:
        raise _Stop(EXIT_RUN_ERROR, f"the gate could not run: {exc}") from exc
    except psycopg.Error as exc:  # the message can quote the URL, so only the type is shown
        raise _Stop(EXIT_RUN_ERROR,
                    f"the gate could not run: database error ({type(exc).__name__})") from exc


def _new_baseline(path: Path, snapshot: Snapshot, question_sha: str, k: int,
                  scores: dict[str, ClassScores]) -> int:
    new = GateBaseline(label=AGENT_DRAFTED_LABEL, arm=VectorArm.name, split=GATED_SPLIT,
                       commit=git_state(), snapshot_sha256=snapshot.sha256,
                       question_set_sha256=question_sha, embedding_model=DEFAULT_MODEL,
                       k=k, scores=scores)
    try:
        return _update(path, new, k)
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"could not write the gate baseline {path}: {exc}") from exc


def main(
    argv: Sequence[str] | None = None,
    *,
    eval_dir: Path = EVAL_DIR,
    snapshot_path: Path = SNAPSHOT_PATH,
    baseline_path: Path = BASELINE_PATH,
    k: int | None = None,
    open_database: OpenDatabase = snapshot_database,
) -> int:
    """CLI entry point.  Exit codes: 0 pass; 2 bad input; 3 could not run; 4 scores dropped.

    Every input is checked against the baseline before the database is opened.
    """
    args = _parse_args(argv)
    k = TOP_K if k is None else k
    try:
        records, snapshot = _inputs(eval_dir, snapshot_path)
        question_sha = question_set_sha256(records)
        baseline = (None if args.update_baseline
                    else _matching_baseline(baseline_path, snapshot, question_sha, k))
        current = _score(open_database, snapshot_path, snapshot, records, k)
        if baseline is None:
            return _new_baseline(baseline_path, snapshot, question_sha, k, current)
        return _check(baseline, current, k)
    except _Stop as stop:
        print(str(stop), file=sys.stderr)
        return stop.code


if __name__ == "__main__":
    sys.exit(main())
