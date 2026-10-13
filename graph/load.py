"""`python -m graph.load`: rebuild the whole graph from its inputs (Step 10).

Offline. Postgres gives the structural rows; `data/extract/` the extract files
(the latest run per filing); `benchmarks/resolution/alias_map.json` the merges;
`benchmarks/extraction/` the chunks extraction failed on (gaps). The builder
combines everything in Python, then one transaction replaces the graph through
the guarded write path, so a failed or rejected write leaves the previous
graph. validate_graph() then checks the result against Neo4j and Postgres,
and a report goes to `benchmarks/graph/load-run{N}.json`.

Usage: python -m graph.load [--rebuild]
Reads NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD and DATABASE_URL. Constraints and
:GraphMeta must exist (apply_constraints()). Exit codes as graph.validate:
0 loaded and valid; 2 bad arguments, or a graph holding data without
--rebuild; 3 could not run (nothing changed); 4 loaded, with violations (the
report is still written).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg
from neo4j import Driver
from neo4j.exceptions import DriverError, Neo4jError

from graph.build_input import (
    ExtractFile,
    InputError,
    StructuralRows,
    latest_extract_files,
    read_extract_file,
)
from graph.builder import BuildError, Built, build_graph
from graph.connection import GraphSettingsMissing, driver_from_env
from graph.constraints import GraphMetaMismatch
from graph.load_report import load_report, next_report_path
from graph.structural import read_structural
from graph.validate import (
    EXIT_OK,
    EXIT_RUN_ERROR,
    EXIT_USAGE,
    EXIT_VIOLATIONS,
    GraphValidationError,
    ValidationReport,
    validate_graph,
)
from graph.write import BatchRejected, GraphNotEmpty, rebuild_graph
from ingest.atomic_json import write_json_atomic
from ingest.provenance import REPO_ROOT, git_state
from resolve.paths import ALIAS_MAP, CANDIDATES_DIR, parse_alias_map

__all__ = ["GraphNotEmpty", "LoadInputs", "LoadResult", "load_graph", "main", "read_inputs"]

EXTRACTION_REPORTS = REPO_ROOT / "benchmarks" / "extraction"
OUTPUT_DIR = REPO_ROOT / "benchmarks" / "graph"


@dataclass(frozen=True, slots=True)
class LoadInputs:
    files: tuple[ExtractFile, ...]
    alias_map: Mapping[str, str]
    alias_map_sha256: str
    gaps: Mapping[str, tuple[str, ...]]  # accession_no -> chunks extraction failed on


@dataclass(frozen=True, slots=True)
class LoadResult:
    built: Built
    write_seconds: float
    validation: ValidationReport


def read_inputs(extract_dir: Path, alias_map: Path, reports_dir: Path) -> LoadInputs:
    """The latest extract file per filing, the alias map and each file's chunk gaps.

    InputError when a file is missing or malformed.
    """
    paths = latest_extract_files(extract_dir)
    if not paths:
        raise InputError(f"no extract files in {extract_dir}")
    files = tuple(read_extract_file(p) for p in paths)
    try:
        raw = alias_map.read_bytes()
        aliases = parse_alias_map(raw, alias_map.name)
    except (OSError, ValueError) as exc:
        raise InputError(f"cannot read the alias map {alias_map.name}: {exc}") from exc
    gaps = {f.accession_no: _failed_chunks(reports_dir / f"{f.accession_no}-run{f.run}.json")
            for f in files}
    return LoadInputs(files, aliases, hashlib.sha256(raw).hexdigest(), gaps)


def _failed_chunks(report: Path) -> tuple[str, ...]:
    """The chunk IDs an extraction run report lists as failed."""
    try:
        failed = json.loads(report.read_text(encoding="utf-8"))["failed_chunks"]
        chunk_ids = [entry["chunk_id"] for entry in failed]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InputError(f"cannot read failed_chunks from {report.name}: {exc!r}") from exc
    if not all(isinstance(c, str) for c in chunk_ids):
        raise InputError(f"{report.name}: a failed chunk ID is not a string")
    return tuple(sorted(chunk_ids))


def load_graph(
    driver: Driver, rows: StructuralRows, inputs: LoadInputs, *, rebuild: bool,
    postgres: psycopg.Connection | None = None,
) -> LoadResult:
    """Build the graph, write it in one transaction and validate it.

    Raises GraphNotEmpty (without *rebuild*), BuildError or BatchRejected, each
    before anything changes. Validation failures come back in the result.
    """
    built = build_graph(rows, inputs.files, inputs.alias_map)
    started = time.monotonic()
    rebuild_graph(driver, built.batch, replace=rebuild)
    seconds = time.monotonic() - started
    try:
        validation = validate_graph(driver, postgres)
    except GraphValidationError as exc:
        validation = exc.report
    return LoadResult(built, seconds, validation)


# ── command ────────────────────────────────────────────────────────────────────

class _CouldNotRun(RuntimeError):
    """The load could not start or finish; the message is safe to print."""


def _run(rebuild: bool) -> tuple[LoadInputs, LoadResult]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise _CouldNotRun("DATABASE_URL is not set")
    try:
        inputs = read_inputs(CANDIDATES_DIR, ALIAS_MAP, EXTRACTION_REPORTS)
        driver = driver_from_env()
    except (InputError, GraphSettingsMissing, ValueError) as exc:  # ValueError: bad NEO4J_URI
        raise _CouldNotRun(str(exc)) from exc
    try:
        with driver, psycopg.connect(url) as conn:
            conn.read_only = True
            driver.verify_connectivity()
            return inputs, load_graph(driver, read_structural(conn), inputs,
                                      rebuild=rebuild, postgres=conn)
    except (BuildError, BatchRejected, GraphMetaMismatch, Neo4jError, DriverError,
            psycopg.Error) as exc:
        raise _CouldNotRun(f"{type(exc).__name__}: {exc}") from exc


def main(argv: Sequence[str] | None = None, *, out_dir: Path = OUTPUT_DIR) -> int:
    """CLI entry point; exit codes in the module docstring."""
    parser = argparse.ArgumentParser(
        description="Rebuild the graph from Postgres, the extract files and the alias map.")
    parser.add_argument("--rebuild", action="store_true",
                        help="replace a graph that already holds data")
    args = parser.parse_args(argv)
    try:
        inputs, result = _run(args.rebuild)
    except GraphNotEmpty as exc:
        print(f"{exc}; nothing was written. Pass --rebuild to replace it.", file=sys.stderr)
        return EXIT_USAGE
    except _CouldNotRun as exc:
        print(f"graph load could not run; nothing was written: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    path = next_report_path(out_dir)
    write_json_atomic(path, load_report(git_state(), inputs, result), indent=2)
    print(result.validation)
    print(f"load report: {path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path}")
    return EXIT_OK if result.validation.ok else EXIT_VIOLATIONS


if __name__ == "__main__":
    sys.exit(main())
