"""`python -m graph.global_provenance <run file>`: the Step 12 provenance check.

Reads a graph_global run and traces every citation of every kept answer
sentence back through the graph: the citation holds when one of the
question's selected communities has a member with ``EVIDENCED_BY`` to the cited
chunk, or an extracted edge between two of its members with the chunk in
``chunk_ids``.  A top community's members are its lower communities' members.
Dropped sentences are not checked; the answer filter already removed them.

Beside that count (the gate), a strict count (Step 13): a kept citation passes
only through a community whose kept map point, sent to the reduce call, cites
the chunk.  A citation no such point names breaks the strict count.

The graph must be the one the run read: the run header's load run must be the
latest graph load report, or one whose report matches it in everything but its
commit and write time (a rebuild of the same inputs).  Results go to
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
from dataclasses import dataclass, replace
from pathlib import Path

from neo4j import Driver
from neo4j.exceptions import DriverError, Neo4jError

from graph.batch import CHUNK, CHUNK_ID, EVIDENCED_BY
from graph.connection import DATABASE, GraphSettingsMissing, driver_from_env, read_session
from graph.cypher import quoted
from graph.load import OUTPUT_DIR as LOAD_REPORT_DIR
from graph.load_report import latest_load_run
from graph.ontology import COMMUNITY, IN_COMMUNITY, PARENT_OF
from graph.step12_spend import GLOBAL_ARM
from graph.validate import EXIT_OK, EXIT_RUN_ERROR, EXIT_USAGE, EXIT_VIOLATIONS
from ingest.atomic_json import write_json_atomic
from ingest.provenance import REPO_ROOT, git_state

LABEL = "agent-drafted questions"
STRICT_RULE = ("a kept citation passes only through a community whose kept map point in the "
               "reduce input cites the chunk; reported beside the gate, not a gate")
REPORT_DIR = REPO_ROOT / "benchmarks" / "graph"

_REPORT = re.compile(r"global-provenance-run(\d+)\.json")
_IN, _PARENT = quoted(IN_COMMUNITY), quoted(PARENT_OF)
_C, _CHUNK, _EVIDENCE = quoted(COMMUNITY), quoted(CHUNK), quoted(EVIDENCED_BY)
# A selected community's members: its own, or a top community's through its children.
_MEMBERS = (
    "UNWIND $ids AS cid "
    f"MATCH (s:{_C} {{community_id: cid}}) "
    f"OPTIONAL MATCH (s)-[:{_PARENT}*0..1]->(:{_C})<-[:{_IN}]-(m) "
    "RETURN cid, collect(DISTINCT elementId(m)) AS members"
)
_NODE_EVIDENCE = (
    f"MATCH (m)-[:{_EVIDENCE}]->(c:{_CHUNK}) "
    f"WHERE elementId(m) IN $members AND c.{CHUNK_ID} IN $chunks "
    f"RETURN elementId(m) AS member, m.key AS key, c.{CHUNK_ID} AS chunk_id"
)
_EDGE_EVIDENCE = (
    "MATCH (a)-[r]->(b) WHERE elementId(a) IN $members AND elementId(b) IN $members "
    "AND r.chunk_ids IS NOT NULL "
    "UNWIND [c IN r.chunk_ids WHERE c IN $chunks] AS chunk_id "
    "RETURN elementId(a) AS a, elementId(b) AS b, a.key AS start, type(r) AS type, "
    "b.key AS end, chunk_id"
)


class RunFileError(ValueError):
    """The run file is missing, unreadable or not a graph_global run."""


# Load report fields that differ between two loads of the same inputs.
_RUN_FIELDS = frozenset({"commit", "write_seconds"})


@dataclass(frozen=True)
class Citation:
    question_id: str
    sentence: str
    chunk_id: str
    selected: tuple[str, ...]  # community IDs, by rank
    citing: tuple[str, ...] = ()  # communities whose kept, reduced map points cite the chunk


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
    if header.get("arm") != GLOBAL_ARM:
        raise RunFileError(f"{path.name} is a {header.get('arm')!r} run, not {GLOBAL_ARM}")
    try:
        citations = []
        for q in questions:
            calls = q["graph"]["global_search"]["map_calls"]
            selected = tuple(c["community_id"] for c in calls)
            citations += [Citation(q["id"], s["text"], chunk_id, selected,
                                   _citing(calls, chunk_id))
                          for s in q["answer"]["kept"] for chunk_id in s["citations"]]
        return header, citations
    except (KeyError, TypeError) as exc:
        raise RunFileError(f"{path.name}: a question lacks {exc}") from exc


def _citing(map_calls: Sequence[Mapping], chunk_id: str) -> tuple[str, ...]:
    """The communities, by rank, with a kept map point in the reduce input citing *chunk_id*."""
    return tuple(c["community_id"] for c in map_calls
                 if any(p.get("kept") and p.get("in_reduce") and chunk_id in p["citations"]
                        for p in c.get("points", ())))


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
    """Each named community's member evidence for *chunk_ids*, read in read transactions.

    Raises RuntimeError naming any community the graph does not hold.
    """
    with read_session(driver, database) as session:
        def read(cypher: str, **params: object) -> list[dict]:
            return session.execute_read(lambda tx: [r.data() for r in tx.run(cypher, params)])

        members = {r["cid"]: set(r["members"]) for r in read(_MEMBERS, ids=list(community_ids))}
        missing = sorted(set(community_ids) - set(members))
        if missing:
            raise RuntimeError(f"the graph holds no community {', '.join(missing)}")
        every = sorted(set().union(*members.values())) if members else []
        nodes = read(_NODE_EVIDENCE, members=every, chunks=list(chunk_ids))
        edges = read(_EDGE_EVIDENCE, members=every, chunks=list(chunk_ids))
    return {cid: _evidence(ids, nodes, edges) for cid, ids in members.items()}


def _evidence(members: set[str], nodes: Sequence[Mapping], edges: Sequence[Mapping]) -> Evidence:
    """The rows that fall inside one community: a node among *members*, or an edge with both
    ends among them."""
    by_chunk: dict[str, set[str]] = {}
    for row in nodes:
        if row["member"] in members:
            by_chunk.setdefault(row["chunk_id"], set()).add(row["key"])
    edge_chunk: dict[str, set[tuple[str, str, str]]] = {}
    for row in edges:
        if row["a"] in members and row["b"] in members:
            edge_chunk.setdefault(row["chunk_id"], set()).add(
                (row["start"], row["type"], row["end"]))
    return Evidence({c: tuple(sorted(k)) for c, k in by_chunk.items()},
                    {c: tuple(sorted(e)) for c, e in edge_chunk.items()})


def next_report_path(directory: Path) -> Path:
    used = [int(m[1]) for p in directory.glob("global-provenance-run*.json")
            if (m := _REPORT.fullmatch(p.name))]
    return directory / f"global-provenance-run{max(used, default=0) + 1}.json"


def _check_load_run(header: Mapping, load_reports: Path) -> tuple[str, str]:
    """The run's load run and the latest one, refusing when the latest is another build."""
    run_load = (header.get("graph") or {}).get("load_run")
    latest = latest_load_run(load_reports)
    if run_load != latest and not _same_build(load_reports, run_load, latest):
        raise RuntimeError(f"the run read {run_load} but the latest graph load is {latest}; "
                           "check against the graph the run read")
    return run_load, latest


def _same_build(load_reports: Path, first: str | None, second: str | None) -> bool:
    """Whether two load reports differ only in their commit and write time."""
    if first is None or second is None:
        return False
    try:
        reports = [json.loads((load_reports / f"{name}.json").read_text(encoding="utf-8"))
                   for name in (first, second)]
    except (OSError, ValueError):
        return False
    if not all(isinstance(r, dict) for r in reports):
        return False
    a, b = ({k: v for k, v in r.items() if k not in _RUN_FIELDS} for r in reports)
    return a == b


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
        load_run, checked = _check_load_run(header, load_reports)
        communities = sorted({c for cit in citations for c in cit.selected})
        chunks = sorted({cit.chunk_id for cit in citations})
        with driver_from_env() as driver:
            driver.verify_connectivity()
            evidence = community_evidence(driver, communities, chunks)
        traces = [{"id": c.question_id, "sentence": c.sentence, "chunk_id": c.chunk_id,
                   "selected": list(c.selected), "found": trace(c, evidence)}
                  for c in citations]
        breaks = [t for t in traces if t["found"] is None]
        strict = [{"id": c.question_id, "chunk_id": c.chunk_id,
                   "citing_communities": list(c.citing),
                   "found": trace(replace(c, selected=c.citing), evidence)}
                  for c in citations]
        strict_breaks = sum(t["found"] is None for t in strict)
        path = next_report_path(report_dir)
        write_json_atomic(path, {
            "commit": git_state(), "label": LABEL, "run_file": args.run_file.name,
            "run_sha256": hashlib.sha256(args.run_file.read_bytes()).hexdigest(),
            "load_run": load_run, "checked_load_run": checked,
            "citations": len(traces), "breaks": len(breaks),
            "strict": {"rule": STRICT_RULE, "citations": len(strict),
                       "breaks": strict_breaks, "traces": strict},
            "traces": traces,
        }, indent=2)
    except (RuntimeError, GraphSettingsMissing, OSError, Neo4jError, DriverError) as exc:
        print(f"provenance check could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    for b in breaks:
        print(f"BREAK {b['id']}: {b['chunk_id']} not reached from {', '.join(b['selected'])}")
    print(f"{len(traces)} citations, {len(breaks)} breaks ({LABEL}); strict count: "
          f"{strict_breaks} breaks; report: {path}")
    return EXIT_OK if not breaks else EXIT_VIOLATIONS


if __name__ == "__main__":
    sys.exit(main())
