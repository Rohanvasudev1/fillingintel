"""validate_graph() and `python -m graph.validate` (Step 7; ontology version 2 since Step 8).

The write path checks each batch before it lands; validate_graph() reads the
whole graph back and checks the same ontology and evidence rules (ADR-0004)
however the data got there. It collects every violation before failing and
raises GraphValidationError with a count per check and up to EXAMPLE_LIMIT
examples each.

All graph checks run in one read transaction, so they see one state of the
graph. With a Postgres connection it also checks that each Chunk node's
chunk_id is in the `chunks` table under the same filing; that query is
parameterised. Labels and edge types reach Cypher only from the ontology,
through graph.cypher.quoted(); values are parameters.

Usage: python -m graph.validate
Reads NEO4J_URI, NEO4J_USER and NEO4J_PASSWORD, and DATABASE_URL when set.
Exit codes, as eval.gate: 0 valid; 2 bad arguments; 3 could not run (missing
settings, Neo4j or Postgres unreachable); 4 violations found.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager

import psycopg
from neo4j import (
    READ_ACCESS,
    Driver,
    ManagedTransaction,
    NotificationDisabledClassification,
    Record,
)
from neo4j.exceptions import DriverError, Neo4jError
from neo4j.time import Date

from graph.batch import (
    CHUNK,
    CHUNK_ID,
    CHUNK_IDS,
    CONFIDENCES,
    EVIDENCE_LISTS,
    EVIDENCE_SPANS,
    EVIDENCED_BY,
    EXTRACT_PROMPTS,
)
from graph.connection import DATABASE, GraphSettingsMissing, driver_from_env
from graph.constraints import GRAPH_META, GRAPH_META_QUERY, graph_meta_problem
from graph.cypher import quoted
from graph.ontology import (
    EDGE_TYPES,
    EDGE_TYPES_BY_NAME,
    LABELS,
    LABELS_BY_NAME,
    ONTOLOGY_VERSION,
    Kind,
    Prop,
    PropType,
)
from graph.validation_report import (
    EXAMPLE_LIMIT,
    Check,
    CheckResult,
    Finding,
    GraphValidationError,
    ValidationReport,
    build_report,
)

__all__ = [
    "EXAMPLE_LIMIT", "EXIT_OK", "EXIT_RUN_ERROR", "EXIT_USAGE", "EXIT_VIOLATIONS", "Check",
    "CheckResult", "GraphValidationError", "ValidationReport", "main", "validate_graph",
]

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3
EXIT_VIOLATIONS = 4

# Looked up in the ontology, so renaming one there fails at import, not silently.
_FILING = LABELS_BY_NAME["Filing"].name
_FILED, _COVERS_PERIOD, _PART_OF = (
    EDGE_TYPES_BY_NAME[name].name for name in ("FILED", "COVERS_PERIOD", "PART_OF")
)
_GRAPH_CHECKS = tuple(c for c in Check if c is not Check.POSTGRES)
_LABEL_NAMES = [d.name for d in LABELS]
_EXTRACTED_LABELS = [d.name for d in LABELS if d.kind is Kind.EXTRACTED]
_EDGE_TYPE_NAMES = [d.name for d in EDGE_TYPES]
# Every key property of any label, projected so examples can name the item.
_KEY_NAMES = sorted({k for d in LABELS for k in d.keys})


def _ident(var: str) -> str:
    keys = ", ".join(f".{quoted(k)}" for k in _KEY_NAMES)
    return (f"elementId({var}) AS {var}_id, labels({var}) AS {var}_labels, "
            f"{var} {{{keys}}} AS {var}_keys")


_NODE_RETURN = f"RETURN {_ident('n')}"
_EDGE_RETURN = f"RETURN {_ident('a')}, type(r) AS type, r.role AS role, {_ident('b')}"


def validate_graph(
    driver: Driver, postgres: psycopg.Connection | None = None, database: str = DATABASE,
) -> ValidationReport:
    """Check the whole graph; return the report, or raise GraphValidationError with it.

    The Postgres check runs only when *postgres* is given.
    """
    # The checks name every ontology label, edge type and property, most of which a
    # young graph lacks; the server's "does not exist" (UNRECOGNIZED) notifications would
    # flood stderr. Other classes, such as deprecations, still come through.
    with driver.session(
        database=database, default_access_mode=READ_ACCESS,
        notifications_disabled_classifications=[NotificationDisabledClassification.UNRECOGNIZED],
    ) as session:
        findings, chunks = session.execute_read(_graph_findings)
    checks: tuple[Check, ...] = _GRAPH_CHECKS
    if postgres is not None:
        findings = [*findings, *_postgres_findings(postgres, chunks)]
        checks = (*checks, Check.POSTGRES)
    report = build_report(checks, findings, postgres_checked=postgres is not None)
    if not report.ok:
        raise GraphValidationError(report)
    return report


# chunk_id -> the Chunk node's accession_no as stored, which may be of a wrong type.
ChunkFilings = dict[str, object]


def _graph_findings(tx: ManagedTransaction) -> tuple[list[Finding], ChunkFilings]:
    """Every graph finding, and chunk_id -> accession_no for each Chunk node."""
    chunks = {
        r["chunk_id"]: r["accession_no"]
        for r in tx.run(f"MATCH (c:{quoted(CHUNK)}) "
                        f"RETURN c.{quoted(CHUNK_ID)} AS chunk_id, c.accession_no AS accession_no")
        if isinstance(r["chunk_id"], str)
    }
    findings = [
        *_graph_meta_findings(tx),
        *_label_findings(tx),
        *_edge_type_findings(tx),
        *_endpoint_findings(tx),
        *_node_property_findings(tx),
        *_edge_findings(tx, frozenset(chunks)),
        *_node_evidence_findings(tx),
        *_structure_findings(tx),
    ]
    return findings, chunks


# ── naming items in examples ───────────────────────────────────────────────────

def _node_name(record: Record, var: str) -> str:
    labels = ":".join(record[f"{var}_labels"]) or "(no label)"
    keys = {k: v for k, v in record[f"{var}_keys"].items() if v is not None}
    if not keys:
        return f"{labels}(elementId={record[f'{var}_id']!r})"
    return f"{labels}({', '.join(f'{k}={v!r}' for k, v in sorted(keys.items()))})"


def _edge_name(record: Record) -> str:
    role = f" {{role: {record['role']!r}}}" if record["role"] is not None else ""
    return f"{_node_name(record, 'a')}-[{record['type']}{role}]->{_node_name(record, 'b')}"


# ── graph checks ───────────────────────────────────────────────────────────────

def _graph_meta_findings(tx: ManagedTransaction) -> Iterator[Finding]:
    problem = graph_meta_problem([r.data() for r in tx.run(GRAPH_META_QUERY)], required=True)
    if problem:
        yield Finding(Check.GRAPH_META, problem)


def _label_findings(tx: ManagedTransaction) -> Iterator[Finding]:
    result = tx.run(
        "MATCH (n) WHERE labels(n) <> [$meta] "
        "WITH n, size([l IN labels(n) WHERE l IN $labels]) AS known "
        f"WHERE known <> 1 OR size(labels(n)) <> 1 {_NODE_RETURN}",
        meta=GRAPH_META, labels=_LABEL_NAMES,
    )
    for r in result:
        unknown = [label for label in r["n_labels"] if label not in LABELS_BY_NAME]
        known = len(r["n_labels"]) - len(unknown)
        problems = [f"{known} ontology labels"] if known != 1 else []
        if unknown:
            problems.append(f"unknown labels {', '.join(unknown)}")
        yield Finding(Check.NODE_LABELS, f"{_node_name(r, 'n')}: {'; '.join(problems)}")


def _edge_type_findings(tx: ManagedTransaction) -> Iterator[Finding]:
    result = tx.run(f"MATCH (a)-[r]->(b) WHERE NOT type(r) IN $types {_EDGE_RETURN}",
                    types=_EDGE_TYPE_NAMES)
    for r in result:
        yield Finding(Check.EDGE_TYPES, f"{_edge_name(r)}: {r['type']!r} is not an edge type")


def _endpoint_findings(tx: ManagedTransaction) -> Iterator[Finding]:
    for d in EDGE_TYPES:
        result = tx.run(
            f"MATCH (a)-[r:{quoted(d.name)}]->(b) "
            "WHERE NOT (any(l IN labels(a) WHERE l IN $starts) "
            f"AND any(l IN labels(b) WHERE l IN $ends)) {_EDGE_RETURN}",
            starts=list(d.starts), ends=list(d.ends),
        )
        for r in result:
            yield Finding(Check.ENDPOINTS, f"{_edge_name(r)}: {d.name} goes from "
                                           f"{', '.join(d.starts)} to {', '.join(d.ends)}")


def _stored_as(value: object, prop_type: PropType) -> bool:
    """Whether a value read back from Neo4j has *prop_type*."""
    match prop_type:
        case PropType.STRING:
            return isinstance(value, str)
        case PropType.INTEGER:
            return isinstance(value, int) and not isinstance(value, bool)
        case PropType.FLOAT:
            return isinstance(value, float) and math.isfinite(value)
        case PropType.DATE:
            return isinstance(value, Date)
        case PropType.LIST_STRING:
            return isinstance(value, list) and all(isinstance(v, str) for v in value)
        case _:
            raise AssertionError(f"no stored-type check for {prop_type}")


def _property_problems(props: tuple[Prop, ...], values: Mapping[str, object]) -> list[str]:
    defined = {p.name: p for p in props}
    problems = [f"{name!r} is not in the ontology" for name in values if name not in defined]
    for prop in props:
        value = values.get(prop.name)
        if value is None or (prop.type is PropType.STRING and not str(value).strip()):
            if prop.required:
                problems.append(f"{prop.name} is missing")
        elif not _stored_as(value, prop.type):
            problems.append(f"{prop.name} must be {prop.type}, got {type(value).__name__}")
        elif bad := prop.disallowed(value):
            problems.append(f"{prop.name} {', '.join(map(repr, bad))} not one of "
                            f"{', '.join(prop.values)}")
        elif prop.name == "ontology_version" and value != ONTOLOGY_VERSION:
            problems.append(f"ontology_version is {value}, the code is {ONTOLOGY_VERSION}")
    return problems


def _node_property_findings(tx: ManagedTransaction) -> Iterator[Finding]:
    for d in LABELS:
        for r in tx.run(f"MATCH (n:{quoted(d.name)}) {_NODE_RETURN}, properties(n) AS props"):
            problems = _property_problems(d.properties, r["props"])
            if problems:
                yield Finding(Check.NODE_PROPERTIES, f"{_node_name(r, 'n')}: {'; '.join(problems)}")


# Evidence lists whose entries must not be blank, and how a problem names an entry.
_BLANK_EVIDENCE = {
    EVIDENCE_SPANS: "an evidence span", CONFIDENCES: "a confidence",
    EXTRACT_PROMPTS: "an extract prompt version",
}


def _evidence_problems(props: Mapping[str, object], chunks: frozenset[str]) -> list[str]:
    lists: dict[str, list[str]] = {}
    for name in EVIDENCE_LISTS:
        value = props.get(name)
        if not isinstance(value, list) or not _stored_as(value, PropType.LIST_STRING):
            return []  # reported by the edge property check
        lists[name] = value
    chunk_ids = lists[CHUNK_IDS]
    problems = []
    if not chunk_ids:
        problems.append(f"{CHUNK_IDS} is empty")
    problems += [
        f"{len(chunk_ids)} {CHUNK_IDS} but {len(values)} {name}"
        for name, values in lists.items() if len(values) != len(chunk_ids)
    ]
    if dangling := [c for c in chunk_ids if c not in chunks]:
        problems.append(f"no Chunk node for {', '.join(map(repr, dangling))}")
    if len(set(chunk_ids)) != len(chunk_ids):
        problems.append(f"a chunk is listed twice in {CHUNK_IDS}")
    for name, what in _BLANK_EVIDENCE.items():
        if any(not v.strip() for v in lists[name]):
            problems.append(f"{what} is blank")
    return problems


def _edge_findings(tx: ManagedTransaction, chunks: frozenset[str]) -> Iterator[Finding]:
    """Edge property findings for every edge type, and evidence findings for extracted ones."""
    for d in EDGE_TYPES:
        for r in tx.run(f"MATCH (a)-[r:{quoted(d.name)}]->(b) {_EDGE_RETURN}, "
                        "properties(r) AS props"):
            if problems := _property_problems(d.properties, r["props"]):
                yield Finding(Check.EDGE_PROPERTIES, f"{_edge_name(r)}: {'; '.join(problems)}")
            if d.kind is Kind.EXTRACTED and (problems := _evidence_problems(r["props"], chunks)):
                yield Finding(Check.EDGE_EVIDENCE, f"{_edge_name(r)}: {'; '.join(problems)}")


def _node_evidence_findings(tx: ManagedTransaction) -> Iterator[Finding]:
    result = tx.run(
        "MATCH (n) WHERE any(l IN labels(n) WHERE l IN $extracted) "
        f"AND NOT EXISTS {{ (n)-[:{quoted(EVIDENCED_BY)}]->(:{quoted(CHUNK)}) }} {_NODE_RETURN}",
        extracted=_EXTRACTED_LABELS,
    )
    for r in result:
        yield Finding(Check.NODE_EVIDENCE,
                      f"{_node_name(r, 'n')}: an extracted node needs an {EVIDENCED_BY} edge")


def _structure_findings(tx: ManagedTransaction) -> Iterator[Finding]:
    filings = tx.run(
        f"MATCH (n:{quoted(_FILING)}) "
        f"WITH n, COUNT {{ ()-[:{quoted(_FILED)}]->(n) }} AS filed, "
        f"COUNT {{ (n)-[:{quoted(_COVERS_PERIOD)}]->() }} AS covers "
        f"WHERE filed <> 1 OR covers <> 1 {_NODE_RETURN}, filed, covers",
    )
    for r in filings:
        yield Finding(Check.STRUCTURE, f"{_node_name(r, 'n')}: {r['filed']} {_FILED} and "
                                       f"{r['covers']} {_COVERS_PERIOD} edges; expected one each")
    chunks = tx.run(
        f"MATCH (n:{quoted(CHUNK)}) "
        f"WITH n, COUNT {{ (n)-[:{quoted(_PART_OF)}]->() }} AS part_of, "
        f"[(n)-[:{quoted(_PART_OF)}]->(f:{quoted(_FILING)}) "
        "WHERE f.accession_no <> n.accession_no | f.accession_no] AS other_filings "
        f"WHERE part_of <> 1 OR size(other_filings) > 0 {_NODE_RETURN}, part_of, other_filings",
    )
    for r in chunks:
        problems = [f"{r['part_of']} {_PART_OF} edges; expected one"] if r["part_of"] != 1 else []
        if r["other_filings"]:
            problems.append(f"{_PART_OF} Filing {', '.join(map(repr, r['other_filings']))}, "
                            "not its own accession_no")
        yield Finding(Check.STRUCTURE, f"{_node_name(r, 'n')}: {'; '.join(problems)}")


# ── Postgres ───────────────────────────────────────────────────────────────────

def _postgres_findings(
    conn: psycopg.Connection, chunks: ChunkFilings,
) -> Iterator[Finding]:
    with conn.transaction():
        rows = conn.execute(
            "SELECT chunk_id, accession_no FROM chunks WHERE chunk_id = ANY(%s)", (list(chunks),),
        ).fetchall()
    in_postgres = dict(rows)
    for chunk_id, accession_no in sorted(chunks.items()):
        if chunk_id not in in_postgres:
            yield Finding(Check.POSTGRES, f"Chunk {chunk_id!r} is not in Postgres")
        elif in_postgres[chunk_id] != accession_no:
            yield Finding(Check.POSTGRES,
                          f"Chunk {chunk_id!r} is under {accession_no!r} in the graph but "
                          f"{in_postgres[chunk_id]!r} in Postgres")


# ── command ────────────────────────────────────────────────────────────────────

ConnectPostgres = Callable[[str], AbstractContextManager[psycopg.Connection]]


def _connect_read_only(url: str) -> psycopg.Connection:
    """A Postgres connection whose transactions are read only; validation never writes."""
    conn = psycopg.connect(url)
    conn.read_only = True
    return conn


class _CouldNotRun(RuntimeError):
    """Validation could not start or finish; the message is safe to print."""


def _validate_from_env(connect_postgres: ConnectPostgres) -> ValidationReport:
    try:
        driver = driver_from_env()
    except (GraphSettingsMissing, ValueError) as exc:  # ValueError: a malformed NEO4J_URI
        raise _CouldNotRun(str(exc)) from exc
    with driver:
        try:
            driver.verify_connectivity()
            url = os.environ.get("DATABASE_URL")
            if not url:
                return validate_graph(driver)
            with connect_postgres(url) as conn:
                return validate_graph(driver, conn)
        except (Neo4jError, DriverError, psycopg.Error) as exc:
            raise _CouldNotRun(f"{type(exc).__name__}: {exc}") from exc


def main(argv: Sequence[str] | None = None, *,
         connect_postgres: ConnectPostgres = _connect_read_only) -> int:
    """CLI entry point. Exit codes: 0 valid; 2 bad arguments; 3 could not run; 4 violations."""
    # argparse exits with status 2 (EXIT_USAGE) on bad arguments, as in eval.gate.
    argparse.ArgumentParser(
        description="Check the whole graph against the ontology and evidence rules. "
                    "Uses NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD and, when set, DATABASE_URL.",
    ).parse_args(argv)
    try:
        report = _validate_from_env(connect_postgres)
    except GraphValidationError as exc:
        print(exc.report)
        return EXIT_VIOLATIONS
    except _CouldNotRun as exc:
        print(f"graph validation could not run: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    print(report)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
