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
from typing import Literal, NamedTuple, TypeAlias

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


JudgedBy: TypeAlias = Literal["company", "period"]


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
    by: JudgedBy
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
    by: JudgedBy = "company" if len(record.tickers) > 1 else "period"
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


def _group_of(filing: ChunkFiling, by: JudgedBy) -> str:
    return filing.ticker if by == "company" else filing.fiscal_period


def query_files(directory: Path) -> dict[str, Path]:
    """Question id -> its query file, for every `*.cypher` file in *directory*."""
    return {p.stem: p for p in sorted(directory.glob("*.cypher"))}


def _read(driver: Driver, database: str, cypher: str,
          **params: object) -> list[dict[str, object]]:
    """The rows of *cypher*, run in a read transaction."""
    with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
        return session.execute_read(lambda tx: [r.data() for r in tx.run(cypher, params)])


def run_query(driver: Driver, cypher: str, database: str = DATABASE) -> list[dict[str, object]]:
    """The query's rows, read in a read transaction; each must have a `chunk_ids` list."""
    rows = _read(driver, database, cypher)
    bad = [r for r in rows if not isinstance(r.get(CHUNK_IDS), list)]
    if bad:
        raise QueryError(f"every row needs a {CHUNK_IDS} list; got {bad[0]}")
    return rows


def chunk_filings(driver: Driver, chunk_ids: Sequence[str],
                  database: str = DATABASE) -> dict[str, ChunkFiling]:
    """chunk_id -> its filer's ticker and fiscal period, read from the graph."""
    rows = _read(driver, database, _CHUNK_FILINGS, ids=list(chunk_ids))
    return {str(r["chunk_id"]): ChunkFiling(str(r["ticker"]), str(r["fiscal_period"]))
            for r in rows}


@dataclass(frozen=True)
class QuestionResult:
    record: EvalRecord
    query_file: Path
    cypher: str
    rows: list[dict[str, object]]
    verdict: Verdict

    @property
    def returned(self) -> list[str]:
        return sorted({str(c) for row in self.rows for c in row[CHUNK_IDS]})  # type: ignore[union-attr]

    def summary(self) -> str:
        groups = ", ".join(f"{g.group} {'hit' if g.passed else 'MISS'}"
                           for g in self.verdict.groups)
        return f"{self.record.id}: {'PASS' if self.verdict.passed else 'FAIL'} ({groups})"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.record.id,
            "question": self.record.question,
            "query_file": self.query_file.name,
            "query_sha256": hashlib.sha256(self.cypher.encode("utf-8")).hexdigest(),
            "rows": self.rows,
            "returned_chunk_ids": self.returned,
            "judged_by": self.verdict.by,
            "groups": [{"group": g.group, "gold": list(g.gold), "hits": list(g.hits),
                        "passed": g.passed} for g in self.verdict.groups],
            "passed": self.verdict.passed,
        }


def check_question(driver: Driver, record: EvalRecord, path: Path) -> QuestionResult:
    """Run one query file and judge it against *record*."""
    cypher = path.read_text(encoding="utf-8")
    rows = run_query(driver, cypher)
    returned = {c for row in rows for c in row[CHUNK_IDS]}  # type: ignore[union-attr]
    verdict = judge(record, returned, chunk_filings(driver, record.gold_chunk_ids))
    return QuestionResult(record, path, cypher, rows, verdict)


def next_report_path(directory: Path) -> Path:
    used = [int(m[1]) for p in directory.glob("multihop-run*.json")
            if (m := _REPORT.fullmatch(p.name))]
    return directory / f"multihop-run{max(used, default=0) + 1}.json"


def _records_for(files: Mapping[str, Path], eval_set: Path) -> dict[str, EvalRecord]:
    if not files:
        raise QueryError("no *.cypher query files found")
    records = {r.id: r for r in load_records(eval_set)}
    missing = sorted(set(files) - set(records))
    if missing:
        raise QueryError(f"query files name no eval record: {missing}")
    wrong = [q for q in files if (records[q].class_, records[q].split) != ("multi_hop", "dev")]
    if wrong:
        raise QueryError(f"not dev multi_hop questions: {sorted(wrong)}")
    return {q: records[q] for q in files}


def main(argv: Sequence[str] | None = None, *, query_dir: Path = QUERY_DIR,
         report_dir: Path = REPORT_DIR, eval_set: Path = EVAL_SET) -> int:
    """CLI entry point. Exit codes: 0 all passed; 2 bad arguments; 3 could not run; 4 a failure."""
    argparse.ArgumentParser(
        description="Run the hand-written multi-hop Cypher queries read-only and apply the "
                    "pass rule. Uses NEO4J_URI, NEO4J_USER and NEO4J_PASSWORD.",
    ).parse_args(argv)
    try:
        files = query_files(query_dir)
        records = _records_for(files, eval_set)
        with driver_from_env() as driver:
            driver.verify_connectivity()
            results = [check_question(driver, records[q], path) for q, path in files.items()]
        passed = sum(r.verdict.passed for r in results)
        path = next_report_path(report_dir)
        write_json_atomic(path, {
            "commit": git_state(), "label": LABEL, "query_dir": query_dir.name,
            "questions": [r.to_dict() for r in results],
            "passed": passed, "total": len(results),
        }, indent=2)
    except (QueryError, GraphSettingsMissing, OSError, ValueError, Neo4jError,
            DriverError) as exc:
        print(f"multi-hop check could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    for result in results:
        print(result.summary())
    print(f"{passed} of {len(results)} passed ({LABEL}); report: {path}")
    return EXIT_OK if passed == len(results) else EXIT_VIOLATIONS


if __name__ == "__main__":
    sys.exit(main())
