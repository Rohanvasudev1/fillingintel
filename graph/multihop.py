"""`python -m graph.multihop`: the Step 10 multi-hop check.

Each file `benchmarks/graph/multihop/{question id}.cypher` holds a hand-written
query for one agent-drafted dev multi_hop question. The runner executes each in
a read transaction and applies the pass rule: for each company the question
asks about (or, for a one-company question, each fiscal period), the chunk IDs
in the query's `chunk_ids` column include at least one of the record's gold
chunks for that company or period. A gold chunk's company and period come from
the graph (Chunk -PART_OF-> Filing <-FILED- Company).

A failing question is a finding about the ontology; the question and the pass
rule are never changed to make it pass. Results go to
`benchmarks/graph/multihop-run{N}.json`, labelled agent-drafted.

Usage: python -m graph.multihop
Reads NEO4J_URI, NEO4J_USER and NEO4J_PASSWORD. Exit codes as graph.validate:
0 every question passed; 2 bad arguments; 3 could not run; 4 a question failed.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple

from neo4j import READ_ACCESS, Driver
from neo4j.exceptions import DriverError, Neo4jError

from eval.schema import EvalRecord, load_records
from graph.connection import DATABASE, GraphSettingsMissing, driver_from_env
from graph.validate import EXIT_OK, EXIT_RUN_ERROR, EXIT_VIOLATIONS
from ingest.atomic_json import write_json_atomic
from ingest.provenance import REPO_ROOT, git_state

QUERY_DIR = REPO_ROOT / "benchmarks" / "graph" / "multihop"
REPORT_DIR = REPO_ROOT / "benchmarks" / "graph"
EVAL_SET = REPO_ROOT / "eval" / "agent_drafted_set.jsonl"
CHUNK_IDS = "chunk_ids"
LABEL = "agent-drafted questions"

_REPORT = re.compile(r"multihop-run(\d+)\.json")
_CHUNK_FILINGS = (
    "MATCH (co:Company)-[:FILED]->(f:Filing)<-[:PART_OF]-(c:Chunk) "
    "WHERE c.chunk_id IN $ids "
    "RETURN c.chunk_id AS chunk_id, co.ticker AS ticker, f.fiscal_period AS fiscal_period"
)


class QueryError(RuntimeError):
    """A query or its question cannot be judged; the message says why."""


class ChunkFiling(NamedTuple):
    ticker: str
    fiscal_period: str


@dataclass(frozen=True)
class GroupResult:
    group: str  # a ticker or a fiscal period
    gold: tuple[str, ...]
    hits: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return bool(self.hits)


@dataclass(frozen=True)
class Verdict:
    by: Literal["company", "period"]
    groups: tuple[GroupResult, ...]

    @property
    def passed(self) -> bool:
        return all(g.passed for g in self.groups)


def judge(record: EvalRecord, returned: Iterable[str],
          gold_filings: Mapping[str, ChunkFiling]) -> Verdict:
    """The pass rule for *record* given the chunk IDs a query returned.

    A question naming several companies is judged per company; a one-company
    question per fiscal period. Raises QueryError when a gold chunk's filing is
    unknown or an asked company or period has no gold chunk.
    """
    by: Literal["company", "period"] = "company" if len(record.tickers) > 1 else "period"
    asked = record.tickers if by == "company" else record.fiscal_periods
    unknown = [c for c in record.gold_chunk_ids if c not in gold_filings]
    if unknown:
        raise QueryError(f"{record.id}: no filing found for gold chunks {unknown}")
    returned_set = set(returned)
    groups = []
    for group in sorted(asked):
        gold = tuple(c for c in record.gold_chunk_ids
                     if _group_of(gold_filings[c], by) == group)
        if not gold:
            raise QueryError(f"{record.id}: {group} is asked about but has no gold chunk")
        groups.append(GroupResult(group, gold, tuple(c for c in gold if c in returned_set)))
    return Verdict(by, tuple(groups))


def _group_of(filing: ChunkFiling, by: str) -> str:
    return filing.ticker if by == "company" else filing.fiscal_period


def query_files(directory: Path) -> dict[str, Path]:
    """Question id -> its query file, for every `*.cypher` file in *directory*."""
    return {p.stem: p for p in sorted(directory.glob("*.cypher"))}


def run_query(driver: Driver, cypher: str, database: str = DATABASE) -> list[dict[str, object]]:
    """The query's rows, read in a read transaction; each must have a `chunk_ids` list."""
    def read(tx):
        return [r.data() for r in tx.run(cypher)]

    with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
        rows = session.execute_read(read)
    bad = [r for r in rows if not isinstance(r.get(CHUNK_IDS), list)]
    if bad:
        raise QueryError(f"every row needs a {CHUNK_IDS} list; got {bad[0]}")
    return rows


def chunk_filings(driver: Driver, chunk_ids: Sequence[str],
                  database: str = DATABASE) -> dict[str, ChunkFiling]:
    """chunk_id -> its filer's ticker and fiscal period, read from the graph."""
    with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
        rows = session.execute_read(lambda tx: [r.data() for r in tx.run(
            _CHUNK_FILINGS, ids=list(chunk_ids))])
    return {r["chunk_id"]: ChunkFiling(r["ticker"], r["fiscal_period"]) for r in rows}


def check_question(driver: Driver, record: EvalRecord, path: Path) -> dict[str, object]:
    """Run one query file and judge it; the report entry for *record*."""
    cypher = path.read_text(encoding="utf-8")
    rows = run_query(driver, cypher)
    returned = sorted({c for row in rows for c in row[CHUNK_IDS]})
    verdict = judge(record, returned, chunk_filings(driver, record.gold_chunk_ids))
    return {
        "id": record.id,
        "question": record.question,
        "query_file": path.relative_to(REPO_ROOT).as_posix(),
        "query_sha256": hashlib.sha256(cypher.encode("utf-8")).hexdigest(),
        "rows": rows,
        "returned_chunk_ids": returned,
        "judged_by": verdict.by,
        "groups": [{"group": g.group, "gold": list(g.gold), "hits": list(g.hits),
                    "passed": g.passed} for g in verdict.groups],
        "passed": verdict.passed,
    }


def next_report_path(directory: Path) -> Path:
    used = [int(m[1]) for p in directory.glob("multihop-run*.json")
            if (m := _REPORT.fullmatch(p.name))]
    return directory / f"multihop-run{max(used, default=0) + 1}.json"


def _records_for(files: Mapping[str, Path]) -> dict[str, EvalRecord]:
    records = {r.id: r for r in load_records(EVAL_SET)}
    missing = sorted(set(files) - set(records))
    if missing:
        raise QueryError(f"query files name no eval record: {missing}")
    wrong = [q for q in files if (records[q].class_, records[q].split) != ("multi_hop", "dev")]
    if wrong:
        raise QueryError(f"not dev multi_hop questions: {sorted(wrong)}")
    return {q: records[q] for q in files}


def _summary(entry: Mapping[str, object]) -> str:
    groups = ", ".join(f"{g['group']} {'hit' if g['passed'] else 'MISS'}"
                       for g in entry["groups"])  # type: ignore[union-attr]
    return f"{entry['id']}: {'PASS' if entry['passed'] else 'FAIL'} ({groups})"


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Exit codes: 0 all passed; 2 bad arguments; 3 could not run; 4 a failure."""
    argparse.ArgumentParser(
        description="Run the hand-written multi-hop Cypher queries read-only and apply the "
                    "pass rule. Uses NEO4J_URI, NEO4J_USER and NEO4J_PASSWORD.",
    ).parse_args(argv)
    files = query_files(QUERY_DIR)
    try:
        records = _records_for(files)
        with driver_from_env() as driver:
            driver.verify_connectivity()
            entries = [check_question(driver, records[q], path) for q, path in files.items()]
    except (QueryError, GraphSettingsMissing, ValueError, Neo4jError, DriverError) as exc:
        print(f"multi-hop check could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    report = {"commit": git_state(), "label": LABEL, "questions": entries,
              "passed": sum(bool(e["passed"]) for e in entries), "total": len(entries)}
    path = next_report_path(REPORT_DIR)
    write_json_atomic(path, report, indent=2)
    for entry in entries:
        print(_summary(entry))
    print(f"{report['passed']} of {report['total']} passed ({LABEL}); report: "
          f"{path.relative_to(REPO_ROOT).as_posix()}")
    return EXIT_OK if report["passed"] == report["total"] else EXIT_VIOLATIONS


if __name__ == "__main__":
    sys.exit(main())
