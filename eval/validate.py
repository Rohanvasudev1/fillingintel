"""Validate an eval-set JSONL file against the corpus (Step 4).

Usage::

    uv run python -m eval.validate eval/eval_set.jsonl
    uv run python -m eval.validate eval/agent_drafted_set.jsonl

``eval/eval_set.jsonl`` is the human set: every record must be
``human_written`` or ``human_verified``, and each ``human_verified`` record
must have an accept in ``eval/review_log.jsonl``.  The data cannot prove a
human wrote a ``human_written`` record; that rests on commit history.
Any other file is a drafted set: every record must be ``agent_drafted``.
Exit codes: 0 valid, 1 problems found, 2 the file or corpus could not be read.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path

import psycopg

from eval.corpus_index import ChunkIndex, ChunkInfo
from eval.review_log import accepted_keys
from eval.schema import (
    HUMAN_PROVENANCE,
    NO_GOLD_CLASSES,
    EvalRecord,
    load_records,
    normalise_question,
    split_for,
)
from ingest.parsed_files import DEFAULT_PARSED_DIR, text_sha256
from ingest.provenance import REPO_ROOT
from ingest.store import ChunkNotFound, resolve

HUMAN_SET_NAME = "eval_set.jsonl"
DEFAULT_REVIEW_LOG = REPO_ROOT / "eval" / "review_log.jsonl"
AGENT_AUTHOR_PREFIX = "claude-"  # agent authors are "claude-subagent:<slice>"
_SCOPE_BY_COUNT = {0: "none", 1: "single", 2: "two"}  # three or more is "multi"
# Evidence scopes each class may use (RUNBOOK Step 4 class definitions).
_SCOPES_BY_CLASS = {
    "lookup": {"single"},
    "local": {"single", "two"},
    "multi_hop": {"two", "multi"},
    "global": {"multi"},
    "decline": {"none"},
    "unanswerable": {"none"},
}
EXIT_PROBLEMS = 1
EXIT_UNREADABLE = 2


def _provenance(
    record: EvalRecord, human_set: bool, accepted_keys: set[str] | None
) -> list[str]:
    if human_set and record.provenance not in HUMAN_PROVENANCE:
        return [f"{record.id}: {record.provenance} record in the human set"]
    if not human_set and record.provenance != "agent_drafted":
        return [f"{record.id}: {record.provenance} record in a drafted set"]
    is_agent = record.author.startswith(AGENT_AUTHOR_PREFIX)
    if is_agent != (record.provenance == "agent_drafted"):
        return [f"{record.id}: author {record.author!r} does not fit {record.provenance}"]
    if (
        accepted_keys is not None
        and record.provenance == "human_verified"
        and record.derived_from not in accepted_keys
    ):
        return [f"{record.id}: human_verified but no accept for its draft in the review log"]
    return []


def _class_and_split(record: EvalRecord, index: ChunkIndex) -> list[str]:
    problems: list[str] = []
    allowed = _SCOPES_BY_CLASS[record.class_]
    if record.evidence_scope not in allowed:
        problems.append(
            f"{record.id}: class {record.class_} needs evidence_scope in {sorted(allowed)}, "
            f"not {record.evidence_scope}"
        )
    if record.split != split_for(record.question):
        problems.append(f"{record.id}: split {record.split} but the question hashes to "
                        f"{split_for(record.question)}")
    return problems


def _gold(record: EvalRecord, index: ChunkIndex) -> list[str]:
    problems: list[str] = []
    for chunk_id in record.gold_chunk_ids:
        text = index.text(chunk_id)
        if text is None:
            problems.append(f"{record.id}: gold chunk {chunk_id} does not resolve")
        elif text_sha256(text) != record.gold_text_sha256[chunk_id]:
            problems.append(f"{record.id}: gold chunk {chunk_id} text changed since labelling")
    expected_scope = _SCOPE_BY_COUNT.get(len(record.gold_chunk_ids), "multi")
    if record.evidence_scope != expected_scope:
        problems.append(
            f"{record.id}: evidence_scope {record.evidence_scope} but "
            f"{len(record.gold_chunk_ids)} gold chunks ({expected_scope})"
        )
    return problems


def _labels(record: EvalRecord, index: ChunkIndex) -> list[str]:
    """Ticker and period labels must be exactly those of the gold chunks."""
    unresolved = any(index.info(c) is None for c in record.gold_chunk_ids)
    if record.class_ in NO_GOLD_CLASSES or unresolved:
        return []
    tickers = {t for c in record.gold_chunk_ids if (t := index.ticker(c)) is not None}
    periods = {p for c in record.gold_chunk_ids if (p := index.fiscal_period(c)) is not None}
    problems: list[str] = []
    if set(record.tickers) != tickers:
        problems.append(
            f"{record.id}: tickers {sorted(record.tickers)} but gold is {sorted(tickers)}"
        )
    if set(record.fiscal_periods) != periods:
        problems.append(
            f"{record.id}: fiscal_periods {sorted(record.fiscal_periods)} "
            f"but gold is {sorted(periods)}"
        )
    return problems


def _relation_holds(relation: str, negative: ChunkInfo, gold: list[ChunkInfo]) -> bool:
    if relation == "peer_company":
        return all(negative.ticker != g.ticker for g in gold)
    if relation == "same_company_other_period":
        return any(
            negative.ticker == g.ticker and negative.fiscal_period != g.fiscal_period for g in gold
        )
    if relation == "same_company_same_filing":
        return any(negative.accession_no == g.accession_no for g in gold)
    raise ValueError(f"unknown hard-negative relation {relation!r}")


def _hard_negatives(record: EvalRecord, index: ChunkIndex) -> list[str]:
    problems: list[str] = []
    if record.class_ not in NO_GOLD_CLASSES and not record.hard_negatives:
        problems.append(f"{record.id}: an answerable question needs at least one hard negative")
    gold = [g for c in record.gold_chunk_ids if (g := index.info(c)) is not None]
    complete = len(gold) == len(record.gold_chunk_ids) and bool(gold)
    for neg in record.hard_negatives:
        info = index.info(neg.chunk_id)
        if info is None:
            problems.append(f"{record.id}: hard negative {neg.chunk_id} does not resolve")
        elif complete and not _relation_holds(neg.relation, info, gold):
            problems.append(f"{record.id}: hard negative {neg.chunk_id} is not {neg.relation}")
    return problems


_CHECKS: tuple[Callable[[EvalRecord, ChunkIndex], list[str]], ...] = (
    _class_and_split,
    _gold,
    _labels,
    _hard_negatives,
)


def validate(
    records: list[EvalRecord],
    index: ChunkIndex,
    human_set: bool,
    accepted_keys: set[str] | None = None,
) -> list[str]:
    """Every problem found, one line each; an empty list means the set is valid.

    With *accepted_keys* (draft keys accepted in the review log), every
    ``human_verified`` record must come from one of them.
    """
    problems: list[str] = []
    questions: dict[str, str] = {}
    ids: set[str] = set()
    for record in records:
        if record.id in ids:
            problems.append(f"{record.id}: duplicate id {record.id}")
        ids.add(record.id)
        problems += _provenance(record, human_set, accepted_keys)
        for check in _CHECKS:
            problems += check(record, index)
        key = normalise_question(record.question)
        if key in questions:
            problems.append(f"{record.id}: same question as {questions[key]}")
        questions.setdefault(key, record.id)
    return problems


def check_against_db(records: list[EvalRecord], conn: psycopg.Connection) -> list[str]:
    """Gold and hard-negative chunks checked through ``resolve()`` in Postgres."""
    problems: list[str] = []
    for record in records:
        for chunk_id in record.gold_chunk_ids:
            try:
                text = resolve(conn, chunk_id)
            except ChunkNotFound:
                problems.append(f"{record.id}: gold chunk {chunk_id} is not in the database")
                continue
            if text_sha256(text) != record.gold_text_sha256[chunk_id]:
                problems.append(f"{record.id}: gold chunk {chunk_id} text differs in the database")
        for neg in record.hard_negatives:
            try:
                resolve(conn, neg.chunk_id)
            except ChunkNotFound:
                problems.append(f"{record.id}: hard negative {neg.chunk_id} is not in the database")
    return problems


def _db_problems(records: list[EvalRecord]) -> list[str]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError("--db needs DATABASE_URL (run with: uv run --env-file .env ...)")
    try:
        with psycopg.connect(url) as conn:
            return check_against_db(records, conn)
    except psycopg.Error as exc:
        raise ValueError(f"database check failed: {type(exc).__name__}") from None


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate an eval-set JSONL file.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--parsed-dir", type=Path, default=DEFAULT_PARSED_DIR)
    parser.add_argument(
        "--kind", choices=("auto", "human", "drafted"), default="auto",
        help="auto: eval_set.jsonl is the human set, any other file a drafted set",
    )
    parser.add_argument(
        "--review-log", type=Path, default=DEFAULT_REVIEW_LOG,
        help="human_verified records in the human set must have an accept here",
    )
    parser.add_argument("--db", action="store_true", help="also check chunks through resolve()")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    human_set = args.kind == "human" or (args.kind == "auto" and args.path.name == HUMAN_SET_NAME)
    try:
        records = load_records(args.path)
        index = ChunkIndex.from_parsed_dir(args.parsed_dir)
        keys = accepted_keys(args.review_log) if human_set else None
        problems = validate(records, index, human_set=human_set, accepted_keys=keys)
        if args.db:
            problems += _db_problems(records)
    except (OSError, ValueError) as exc:
        print(f"cannot read input: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE
    for line in problems:
        print(line)
    print(f"{len(records)} records, {len(problems)} problems")
    return EXIT_PROBLEMS if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
