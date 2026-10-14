"""`python -m graph.load`: rebuild the whole graph from its inputs (Step 10).

Offline. Postgres gives the structural rows; `data/extract/` the extract files
(the latest run per filing); `benchmarks/resolution/alias_map.json` the merges;
`benchmarks/extraction/` the chunks extraction failed on (gaps);
`benchmarks/persistence/links.jsonl` the PERSISTS_AS links. The builder
combines everything in Python, then one transaction replaces the graph through
the guarded write path, so a failed or rejected write leaves the previous
graph. validate_graph() then checks the result against Neo4j and Postgres,
and a report goes to `benchmarks/graph/load-run{N}.json`.

Usage: python -m graph.load [--rebuild]
Reads NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD and DATABASE_URL. Constraints and
:GraphMeta must exist (apply_constraints()). With --rebuild, a :GraphMeta
recording an older ontology is moved to the code's in the rebuild transaction,
and the report says so; a newer or unparseable one is refused (exit 3).
Exit codes as graph.validate: 0 loaded and valid; 2 bad arguments, or a graph holding data without
--rebuild (nothing changed); 3 could not run (nothing changed), or the graph was
replaced but validation could not run (the report says so); 4 loaded, with
violations. The report is written whenever the graph was replaced.
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
    current_extract_files,
)
from graph.builder import BuildError, Built, build_graph
from graph.connection import GraphSettingsMissing, driver_from_env
from graph.constraints import ConstraintMismatch, GraphMetaMismatch, prepare_upgrade
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
from persist.links import PersistLink
from persist.paths import LINKS, parse_links
from resolve.paths import ALIAS_MAP, CANDIDATES_DIR, parse_alias_map

__all__ = ["LoadInputs", "LoadResult", "load_graph", "main", "read_inputs"]

EXTRACTION_REPORTS = REPO_ROOT / "benchmarks" / "extraction"
OUTPUT_DIR = REPO_ROOT / "benchmarks" / "graph"


@dataclass(frozen=True, slots=True)
class LoadInputs:
    files: tuple[ExtractFile, ...]
    alias_map: Mapping[str, str]
    alias_map_sha256: str
    gaps: Mapping[str, tuple[str, ...]]  # accession_no -> chunks extraction failed on
    links: tuple[PersistLink, ...]
    links_sha256: str
    extracted_under: tuple[int, ...]  # ontology versions the extraction run reports name


@dataclass(frozen=True, slots=True)
class LoadResult:
    """A written graph and its validation, or why validation could not run after the write."""

    built: Built
    write_seconds: float
    validation: ValidationReport | None
    validation_error: str | None = None
    upgraded_from: int | None = None  # the older ontology :GraphMeta recorded


def read_inputs(
    extract_dir: Path, alias_map: Path, reports_dir: Path, links: Path,
) -> LoadInputs:
    """Per filing its latest extract run under the current prompt, the alias map, each
    run's chunk gaps and the persistence links. InputError when a file is missing or
    malformed.
    """
    files = current_extract_files(extract_dir)
    try:
        raw = alias_map.read_bytes()
        aliases = parse_alias_map(raw, alias_map.name)
    except (OSError, ValueError) as exc:
        raise InputError(f"cannot read the alias map {alias_map.name}: {exc}") from exc
    reports = {f.accession_no: _run_report(reports_dir / f"{f.accession_no}-run{f.run}.json")
               for f in files}
    try:
        raw_links = links.read_bytes()
        persist_links = parse_links(raw_links, links.name)
    except (OSError, ValueError) as exc:
        raise InputError(f"cannot read the link file {links.name}: {exc}") from exc
    return LoadInputs(files, aliases, hashlib.sha256(raw).hexdigest(),
                      {a: failed for a, (failed, _) in reports.items()},
                      persist_links, hashlib.sha256(raw_links).hexdigest(),
                      tuple(sorted({version for _, version in reports.values()})))


def _run_report(report: Path) -> tuple[tuple[str, ...], int]:
    """The chunk IDs an extraction run report lists as failed, and its ontology version."""
    try:
        found = json.loads(report.read_text(encoding="utf-8"))
        chunk_ids = [entry["chunk_id"] for entry in found["failed_chunks"]]
        version = found["ontology_version"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InputError(f"cannot read failed_chunks and ontology_version from "
                         f"{report.name}: {exc!r}") from exc
    if not all(isinstance(c, str) for c in chunk_ids):
        raise InputError(f"{report.name}: a failed chunk ID is not a string")
    if not isinstance(version, int) or isinstance(version, bool):
        raise InputError(f"{report.name}: ontology_version is not an integer")
    return tuple(sorted(chunk_ids)), version


def load_graph(
    driver: Driver, rows: StructuralRows, inputs: LoadInputs, *, rebuild: bool,
    postgres: psycopg.Connection | None = None,
) -> LoadResult:
    """Build the graph, write it in one transaction and validate it.

    Raises GraphNotEmpty (without *rebuild*), BuildError, BatchRejected,
    GraphMetaMismatch or ConstraintMismatch, each before any node changes. With
    *rebuild*, an older :GraphMeta is upgraded in the rebuild transaction; the
    constraints are re-applied just before it (IF NOT EXISTS, so only a missing
    one is created, and it stays if the rebuild then fails).
    Once the write has committed nothing is raised: violations, or an error
    that stopped validation, come back in the result.
    """
    built = build_graph(rows, inputs.files, inputs.alias_map, inputs.links)
    upgraded_from = prepare_upgrade(driver) if rebuild else None
    started = time.monotonic()
    rebuild_graph(driver, built.batch, replace=rebuild, upgrade_from=upgraded_from)
    seconds = time.monotonic() - started
    try:
        return LoadResult(built, seconds, validate_graph(driver, postgres),
                          upgraded_from=upgraded_from)
    except GraphValidationError as exc:
        return LoadResult(built, seconds, exc.report, upgraded_from=upgraded_from)
    except (Neo4jError, DriverError, psycopg.Error) as exc:
        return LoadResult(built, seconds, None, f"{type(exc).__name__}: {exc}", upgraded_from)


# ── command ────────────────────────────────────────────────────────────────────

class _CouldNotRun(RuntimeError):
    """The load could not start or finish; the message is safe to print."""


def _run(rebuild: bool) -> tuple[LoadInputs, LoadResult]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise _CouldNotRun("DATABASE_URL is not set")
    try:
        inputs = read_inputs(CANDIDATES_DIR, ALIAS_MAP, EXTRACTION_REPORTS, LINKS)
    except InputError as exc:
        raise _CouldNotRun(str(exc)) from exc
    try:
        driver = driver_from_env()
    except (GraphSettingsMissing, ValueError) as exc:  # ValueError: a malformed NEO4J_URI
        raise _CouldNotRun(str(exc)) from exc
    try:
        with driver, psycopg.connect(url) as conn:
            conn.read_only = True
            driver.verify_connectivity()
            return inputs, load_graph(driver, read_structural(conn), inputs,
                                      rebuild=rebuild, postgres=conn)
    except (BuildError, BatchRejected, GraphMetaMismatch, ConstraintMismatch, Neo4jError,
            DriverError, psycopg.Error) as exc:
        raise _CouldNotRun(f"{type(exc).__name__}: {exc}") from exc


def main(argv: Sequence[str] | None = None, *, out_dir: Path = OUTPUT_DIR) -> int:
    """CLI entry point; exit codes in the module docstring."""
    parser = argparse.ArgumentParser(
        description="Rebuild the graph from Postgres, the extract files, the alias map "
                    "and the persistence links.")
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
    shown = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
    if result.validation is None:
        print(f"the graph was replaced, but validation could not run: "
              f"{result.validation_error}. Run python -m graph.validate. Report: {shown}",
              file=sys.stderr)
        return EXIT_RUN_ERROR
    print(result.validation)
    print(f"load report: {shown}")
    return EXIT_OK if result.validation.ok else EXIT_VIOLATIONS


if __name__ == "__main__":
    sys.exit(main())
