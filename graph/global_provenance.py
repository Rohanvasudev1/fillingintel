"""`python -m graph.global_provenance <run file>`: the Step 12 provenance check.

Reads a graph_global run and traces every citation of every kept answer
sentence back through the graph: the citation holds when one of the
question's selected communities has a member with ``EVIDENCED_BY`` to the cited
chunk, or an extracted edge between two of its members with the chunk in
``chunk_ids``.  A top community's members are its lower communities' members.
Dropped sentences are not checked; the answer filter already removed them.

The graph must be the one the run read: the run header's load run must be the
latest graph load report.  Results go to
`benchmarks/graph/global-provenance-run{N}.json` with every trace, labelled
agent-drafted.

Usage: python -m graph.global_provenance benchmarks/runs/<run>.json
Reads NEO4J_URI, NEO4J_USER and NEO4J_PASSWORD. Exit codes as graph.validate:
0 no breaks; 2 bad arguments; 3 could not run; 4 breaks found.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from neo4j import Driver
from neo4j.exceptions import DriverError, Neo4jError

from graph.connection import DATABASE, GraphSettingsMissing, driver_from_env, read_session
from graph.load_report import latest_load_run
from graph.validate import EXIT_OK, EXIT_RUN_ERROR, EXIT_USAGE, EXIT_VIOLATIONS
from ingest.atomic_json import write_json_atomic
from ingest.provenance import REPO_ROOT, git_state

ARM = "graph_global"
LABEL = "agent-drafted questions"
REPORT_DIR = REPO_ROOT / "benchmarks" / "graph"
LOAD_REPORT_DIR = REPORT_DIR

_REPORT = re.compile(r"global-provenance-run(\d+)\.json")
_MEMBERS = (
    "UNWIND $ids AS cid "
    "MATCH (s:Community {community_id: cid}) "
    "MATCH (m)-[:IN_COMMUNITY]->(g:Community) WHERE g = s OR (s)-[:PARENT_OF]->(g) "
    "RETURN cid, collect(m.key) AS keys"
)
_NODE_EVIDENCE = (
    "MATCH (m)-[:EVIDENCED_BY]->(c:Chunk) WHERE m.key IN $keys AND c.chunk_id IN $chunks "
    "RETURN m.key AS key, c.chunk_id AS chunk_id"
)
_EDGE_EVIDENCE = (
    "MATCH (a)-[r]->(b) WHERE a.key IN $keys AND b.key IN $keys AND r.chunk_ids IS NOT NULL "
    "UNWIND [c IN r.chunk_ids WHERE c IN $chunks] AS chunk_id "
    "RETURN a.key AS start, type(r) AS type, b.key AS end, chunk_id"
)


class RunFileError(ValueError):
    """The run file is missing, unreadable or not a graph_global run."""


@dataclass(frozen=True)
class Citation:
    question_id: str
    sentence: str
    chunk_id: str
    selected: tuple[str, ...]  # community IDs, by rank


@dataclass(frozen=True)
class Evidence:
    """What a community's members hold for a chunk: member keys and member edges."""

    nodes: Mapping[str, tuple[str, ...]]  # chunk_id -> member keys evidenced by it
    edges: Mapping[str, tuple[tuple[str, str, str], ...]]  # chunk_id -> (start, type, end)


def read_run(path: Path) -> tuple[dict, list[Citation]]:
    """The run's header and every citation of a kept sentence, in question order."""
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
        header, questions = run["header"], run["questions"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RunFileError(f"cannot read {path}: {type(exc).__name__}: {exc}") from exc
    if header.get("arm") != ARM:
        raise RunFileError(f"{path.name} is a {header.get('arm')!r} run, not {ARM}")
    try:
        return header, [
            Citation(q["id"], s["text"], chunk_id,
                     tuple(c["community_id"] for c in q["graph"]["global_search"]["map_calls"]))
            for q in questions for s in q["answer"]["kept"] for chunk_id in s["citations"]]
    except (KeyError, TypeError) as exc:
        raise RunFileError(f"{path.name}: a question lacks {exc}") from exc


def trace(citation: Citation, evidence: Mapping[str, Evidence]) -> dict[str, str] | None:
    """The first selected community, by rank, holding the citation's chunk; None for a break."""
    for community_id in citation.selected:
        found = evidence.get(community_id)
        if found is None:
            continue
        if nodes := found.nodes.get(citation.chunk_id):
            return {"community_id": community_id, "via": "evidenced_by", "node": nodes[0]}
        if edges := found.edges.get(citation.chunk_id):
            start, edge_type, end = edges[0]
            return {"community_id": community_id, "via": "edge", "type": edge_type,
                    "start": start, "end": end}
    return None


def community_evidence(driver: Driver, community_ids: Sequence[str], chunk_ids: Sequence[str],
                       database: str = DATABASE) -> dict[str, Evidence]:
    """Each named community's member evidence for *chunk_ids*, read in read transactions."""
    with read_session(driver, database) as session:
        def read(cypher: str, **params: object) -> list[dict]:
            return session.execute_read(lambda tx: [r.data() for r in tx.run(cypher, params)])

        members = {r["cid"]: r["keys"] for r in read(_MEMBERS, ids=list(community_ids))}
        result = {}
        for community_id, keys in members.items():
            nodes: dict[str, list[str]] = {}
            for row in read(_NODE_EVIDENCE, keys=keys, chunks=list(chunk_ids)):
                nodes.setdefault(row["chunk_id"], []).append(row["key"])
            edges: dict[str, list[tuple[str, str, str]]] = {}
            for row in read(_EDGE_EVIDENCE, keys=keys, chunks=list(chunk_ids)):
                edges.setdefault(row["chunk_id"], []).append(
                    (row["start"], row["type"], row["end"]))
            result[community_id] = Evidence(
                {c: tuple(sorted(k)) for c, k in nodes.items()},
                {c: tuple(sorted(e)) for c, e in edges.items()})
        return result


def next_report_path(directory: Path) -> Path:
    used = [int(m[1]) for p in directory.glob("global-provenance-run*.json")
            if (m := _REPORT.fullmatch(p.name))]
    return directory / f"global-provenance-run{max(used, default=0) + 1}.json"


def _check_load_run(header: Mapping, load_reports: Path) -> str:
    run_load = (header.get("graph") or {}).get("load_run")
    latest = latest_load_run(load_reports)
    if run_load != latest:
        raise RuntimeError(f"the run read {run_load} but the latest graph load is {latest}; "
                           "check against the graph the run read")
    return latest


def main(argv: Sequence[str] | None = None, *, load_reports: Path = LOAD_REPORT_DIR,
         report_dir: Path = REPORT_DIR) -> int:
    """CLI entry point. Exit codes: 0 no breaks; 2 bad arguments; 3 could not run; 4 breaks."""
    parser = argparse.ArgumentParser(
        description="Trace every kept citation of a graph_global run to a selected community. "
                    "Uses NEO4J_URI, NEO4J_USER and NEO4J_PASSWORD.")
    parser.add_argument("run_file", type=Path)
    args = parser.parse_args(argv)
    try:
        header, citations = read_run(args.run_file)
    except RunFileError as exc:
        print(f"provenance check: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        load_run = _check_load_run(header, load_reports)
        communities = sorted({c for cit in citations for c in cit.selected})
        chunks = sorted({cit.chunk_id for cit in citations})
        with driver_from_env() as driver:
            driver.verify_connectivity()
            evidence = community_evidence(driver, communities, chunks)
        traces = [{"id": c.question_id, "sentence": c.sentence, "chunk_id": c.chunk_id,
                   "selected": list(c.selected), "found": trace(c, evidence)}
                  for c in citations]
        breaks = [t for t in traces if t["found"] is None]
        path = next_report_path(report_dir)
        write_json_atomic(path, {
            "commit": git_state(), "label": LABEL, "run_file": args.run_file.name,
            "run_sha256": hashlib.sha256(args.run_file.read_bytes()).hexdigest(),
            "load_run": load_run, "citations": len(traces), "breaks": len(breaks),
            "traces": traces,
        }, indent=2)
    except (RuntimeError, GraphSettingsMissing, OSError, Neo4jError, DriverError) as exc:
        print(f"provenance check could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    for b in breaks:
        print(f"BREAK {b['id']}: {b['chunk_id']} not reached from {', '.join(b['selected'])}")
    print(f"{len(traces)} citations, {len(breaks)} breaks ({LABEL}); report: {path}")
    return EXIT_OK if not breaks else EXIT_VIOLATIONS


if __name__ == "__main__":
    sys.exit(main())
