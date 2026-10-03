"""Load parsed filings into Postgres and verify ``resolve()`` (Step 3c).

Usage (needs ``DATABASE_URL``; no network)::

    uv run --env-file .env python -m ingest.load [--verify]

Reads ``data/parsed/*.json`` (written by ``python -m ingest.corpus``), chunks
each filing and loads it.  ``--verify`` then checks the RUNBOOK stop condition:
a seeded random sample of chunks resolved one by one with ``resolve()``, and
every chunk resolved in bulk, each compared with the text in ``data/parsed``.
The result goes to ``spikes/load_report.txt``.
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

from ingest.chunker import chunk_filing
from ingest.parsed_files import DEFAULT_PARSED_DIR, ParsedRecord, read_parsed_dir, text_sha256
from ingest.provenance import REPO_ROOT, git_state
from ingest.store import (
    SchemaMismatch,
    apply_schema,
    load_filing,
    resolve,
    stored_offsets,
)

logger = logging.getLogger(__name__)

DEFAULT_REPORT_PATH = REPO_ROOT / "spikes" / "load_report.txt"
SAMPLE_SIZE = 20
SAMPLE_SEED = 20261003
EXIT_VERIFY_FAILED = 1
EXIT_USAGE = 2
EXIT_LOAD_ERROR = 3
_ERROR_CHARS = 500


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of checking stored chunks against the parsed text on disk."""

    filings: int
    sample_size: int
    sample_ok: int
    checked: int
    mismatches: tuple[str, ...]  # chunk IDs whose resolved text differs
    order_mismatches: tuple[str, ...]  # accessions whose stored IDs/offsets differ from the chunker
    hash_mismatches: tuple[str, ...]  # accessions whose stored text hash differs from the file
    empty_filings: tuple[str, ...]  # accessions with no chunks at all

    @property
    def passed(self) -> bool:
        """True only if something was checked and nothing differed."""
        nothing_checked = self.filings == 0 or self.checked == 0 or self.sample_size == 0
        problems = (
            self.mismatches or self.order_mismatches or self.hash_mismatches or self.empty_filings
        )
        return not nothing_checked and self.sample_ok == self.sample_size and not problems


def load_records(conn: psycopg.Connection, records: list[ParsedRecord]) -> int:
    """Chunk and load each record; returns the number of chunks loaded."""
    total = 0
    for record in records:
        chunks = chunk_filing(record.filing)
        load_filing(conn, record, chunks)
        logger.info("%s: %d chunks loaded", record.meta.accession_no, len(chunks))
        total += len(chunks)
    return total


def _stored_hash(conn: psycopg.Connection, accession_no: str) -> str | None:
    row = conn.execute(
        "SELECT text_sha256 FROM filings WHERE accession_no = %s", (accession_no,)
    ).fetchone()
    return None if row is None else row[0]


def verify_chunks(conn: psycopg.Connection, records: list[ParsedRecord]) -> VerifyResult:
    """Check every stored chunk with ``resolve()`` against the parsed text on disk.

    For each filing: the stored text hash must match the file, and the stored
    chunk IDs and offsets must match a fresh chunker run.  Every chunk's
    ``resolve()`` result must equal the file text sliced at its stored offsets.
    The seeded sample of 20 is the RUNBOOK stop condition, reported on its own.
    """
    expected: dict[str, str] = {}
    mismatches: list[str] = []
    order_mismatches: list[str] = []
    hash_mismatches: list[str] = []
    empty: list[str] = []
    for record in records:
        accession_no, text = record.meta.accession_no, record.filing.text
        chunks = chunk_filing(record.filing)
        if not chunks:
            empty.append(accession_no)
        if _stored_hash(conn, accession_no) != text_sha256(text):
            hash_mismatches.append(accession_no)
        stored = stored_offsets(conn, accession_no)
        if stored != [(c.chunk_id, c.char_start, c.char_end) for c in chunks]:
            order_mismatches.append(accession_no)
        for chunk_id, start, end in stored:
            expected[chunk_id] = text[start:end]
            if resolve(conn, chunk_id) != expected[chunk_id]:
                mismatches.append(chunk_id)
    population = sorted(expected)
    sample = random.Random(SAMPLE_SEED).sample(population, min(SAMPLE_SIZE, len(population)))
    sample_ok = sum(1 for chunk_id in sample if resolve(conn, chunk_id) == expected[chunk_id])
    return VerifyResult(
        filings=len(records),
        sample_size=len(sample),
        sample_ok=sample_ok,
        checked=len(population),
        mismatches=tuple(mismatches),
        order_mismatches=tuple(order_mismatches),
        hash_mismatches=tuple(hash_mismatches),
        empty_filings=tuple(empty),
    )


def _display_path(path: Path) -> str:
    """*path* relative to the repo when inside it, so reports carry no home directory."""
    resolved = path.resolve()
    return str(resolved.relative_to(REPO_ROOT)) if resolved.is_relative_to(REPO_ROOT) else str(path)


def format_verify_report(result: VerifyResult, loaded: bool, parsed_dir: Path) -> str:
    """The text of ``spikes/load_report.txt``."""
    lines = [
        "# Load and resolve() verification",
        "",
        f"- commit: {git_state()}",
        f"- parsed dir: {_display_path(parsed_dir)} ({result.filings} files)",
        f"- filings loaded: {result.filings}" if loaded else "- filings loaded: none (verify only)",
        f"- random sample: {result.sample_ok} of {result.sample_size} resolved exactly "
        f"(seed {SAMPLE_SEED})",
        f"- all chunks via resolve(): {result.checked} checked, "
        f"mismatches: {len(result.mismatches)}",
        f"- filings whose stored chunk IDs or offsets differ from the chunker: "
        f"{len(result.order_mismatches)}",
        f"- filings whose stored text hash differs from the file: {len(result.hash_mismatches)}",
        f"- filings with no chunks: {len(result.empty_filings)}",
        f"- result: {'PASS' if result.passed else 'FAIL'}",
    ]
    problems = [
        *(f"- chunk {c}" for c in result.mismatches[:50]),
        *(f"- filing {a}: chunk IDs or offsets differ" for a in result.order_mismatches),
        *(f"- filing {a}: text hash differs" for a in result.hash_mismatches),
        *(f"- filing {a}: no chunks" for a in result.empty_filings),
    ]
    if problems:
        lines += ["", "## Problems", "", *problems]
    return "\n".join(lines) + "\n"


def run(
    conn: psycopg.Connection,
    parsed_dir: Path,
    verify: bool,
    report_path: Path | None,
    load: bool = True,
) -> int:
    """Load and optionally verify; returns 0, 1 if verification fails, 2 if there is no input."""
    records = read_parsed_dir(parsed_dir)
    if not records:
        print(f"no parsed filings in {parsed_dir}; run python -m ingest.corpus", file=sys.stderr)
        return EXIT_USAGE
    if load:
        apply_schema(conn)
        n_chunks = load_records(conn, records)
        logger.info("loaded %d filings, %d chunks", len(records), n_chunks)
    if not verify:
        return 0
    result = verify_chunks(conn, records)
    text = format_verify_report(result, load, parsed_dir)
    print(text)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text, encoding="utf-8")
    return 0 if result.passed else EXIT_VERIFY_FAILED


def _connect(url: str) -> psycopg.Connection | None:
    """Open a connection; on failure print only the error type (messages can quote the URL)."""
    try:
        return psycopg.connect(url)
    except psycopg.Error as exc:
        print(f"could not connect to the database: {type(exc).__name__}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Exit codes: 0 ok; 1 verification failed; 2 bad input (no DATABASE_URL, no
    parsed files, or a database that refuses the connection); 3 load error.
    """
    parser = argparse.ArgumentParser(description="Load data/parsed into Postgres.")
    parser.add_argument("--parsed-dir", type=Path, default=DEFAULT_PARSED_DIR)
    parser.add_argument("--verify", action="store_true", help="check resolve() after loading")
    parser.add_argument(
        "--verify-only", action="store_true", help="check resolve() without reloading"
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set (run with: uv run --env-file .env ...)", file=sys.stderr)
        return EXIT_USAGE
    if not args.parsed_dir.is_dir():
        print(f"parsed directory not found: {args.parsed_dir}", file=sys.stderr)
        return EXIT_USAGE
    conn = _connect(url)
    if conn is None:
        return EXIT_USAGE
    with conn:
        try:
            return run(
                conn, args.parsed_dir, args.verify or args.verify_only, args.report,
                load=not args.verify_only,
            )
        except (psycopg.Error, ValueError, SchemaMismatch) as exc:
            # Truncated: a constraint error quotes the failing row, filing text included.
            logger.error(
                "load failed (%s): %.*s; filings committed before the error stay loaded",
                type(exc).__name__, _ERROR_CHARS, exc,
            )
            return EXIT_LOAD_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
