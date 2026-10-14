"""Tests for the Step 10 multi-hop check (graph/multihop.py).

The pass rule is tested on the real agent-drafted records with chunk metadata
from the corpus filing list. Running a query needs the test database and skips
without NEO4J_TEST_URI.
"""
import json
import os
from pathlib import Path

import pytest
from neo4j.exceptions import Neo4jError

from eval.schema import EvalRecord, load_records
from graph.multihop import (
    LABEL,
    QUERY_DIR,
    ChunkFiling,
    QueryError,
    chunk_filings,
    judge,
    main,
    query_files,
    run_query,
)
from graph.validate import EXIT_OK, EXIT_RUN_ERROR, EXIT_VIOLATIONS
from ingest.provenance import REPO_ROOT
from tests.corpus_filings import corpus_filings

RECORDS = {r.id: r for r in load_records(REPO_ROOT / "eval" / "agent_drafted_set.jsonl")}
FILINGS = {f.accession_no: ChunkFiling(f.ticker, f.fiscal_period) for f in corpus_filings()}

COMPETITORS = RECORDS["q0120"]  # three companies, one gold chunk each
NVDA_RISK = RECORDS["q0127"]    # one company, two periods


def _filings(chunk_ids) -> dict[str, ChunkFiling]:
    return {c: FILINGS[c.split(":")[0]] for c in chunk_ids}


def _judge(record: EvalRecord, returned: list[str]):
    return judge(record, returned, _filings([*record.gold_chunk_ids, *returned]))


def test_every_company_hit_passes():
    verdict = _judge(COMPETITORS, list(COMPETITORS.gold_chunk_ids))
    assert verdict.passed
    assert verdict.by == "company"
    assert [g.group for g in verdict.groups] == ["AMD", "INTC", "NVDA"]


def test_a_company_without_a_gold_hit_fails():
    returned = [c for c in COMPETITORS.gold_chunk_ids if not c.startswith("0000050863-")]
    verdict = _judge(COMPETITORS, returned)
    assert not verdict.passed
    assert [g.group for g in verdict.groups if not g.passed] == ["INTC"]


def test_a_single_company_question_is_judged_per_period():
    fy2026_only = [c for c in NVDA_RISK.gold_chunk_ids if c.startswith("0001045810-26-")]
    verdict = _judge(NVDA_RISK, fy2026_only)
    assert verdict.by == "period"
    assert {g.group: g.passed for g in verdict.groups} == {"FY2025": False, "FY2026": True}
    assert not verdict.passed


def test_a_non_gold_chunk_from_the_right_filing_does_not_count():
    hard_negative = NVDA_RISK.hard_negatives[0].chunk_id  # FY2025 10-K, same filing as gold
    fy2026 = [c for c in NVDA_RISK.gold_chunk_ids if c.startswith("0001045810-26-")]
    verdict = _judge(NVDA_RISK, [hard_negative, *fy2026])
    assert {g.group: g.hits for g in verdict.groups}["FY2025"] == ()
    assert not verdict.passed


def test_an_asked_group_with_no_gold_chunk_cannot_be_judged():
    record = NVDA_RISK.model_copy(update={"fiscal_periods": ["FY2024", "FY2025", "FY2026"]})
    with pytest.raises(QueryError, match="FY2024"):
        _judge(record, list(record.gold_chunk_ids))


def test_committed_queries_name_dev_multi_hop_agent_drafted_questions():
    files = query_files(QUERY_DIR)
    assert sorted(files) == ["q0111", "q0120", "q0127"]
    for qid in files:
        record = RECORDS[qid]
        assert (record.class_, record.split, record.provenance) == (
            "multi_hop", "dev", "agent_drafted")


def test_run_query_returns_rows(neo4j_driver):
    rows = run_query(neo4j_driver, "RETURN ['a:0001', 'b:0002'] AS chunk_ids, 1 AS n")
    assert rows == [{"chunk_ids": ["a:0001", "b:0002"], "n": 1}]


def test_run_query_needs_a_chunk_ids_column(neo4j_driver):
    with pytest.raises(QueryError, match="chunk_ids"):
        run_query(neo4j_driver, "RETURN 1 AS n")


def test_run_query_is_read_only(neo4j_driver):
    with pytest.raises(Neo4jError):
        run_query(neo4j_driver, "CREATE (n:Scratch) RETURN [] AS chunk_ids")
    records, _, _ = neo4j_driver.execute_query("MATCH (n:Scratch) RETURN count(n) AS n")
    assert records[0]["n"] == 0


# ── the graph read and the command ────────────────────────────────────────────

NVDA_GOLD, AMD_GOLD, INTC_GOLD = COMPETITORS.gold_chunk_ids


def _structural(driver) -> None:
    """Company -FILED-> Filing <-PART_OF- Chunk for q0120's three gold chunks."""
    driver.execute_query(
        "UNWIND $rows AS row "
        "CREATE (:Company {ticker: row.ticker})-[:FILED]->"
        "(f:Filing {fiscal_period: row.period})<-[:PART_OF]-(:Chunk {chunk_id: row.chunk})",
        rows=[{"ticker": "NVDA", "period": "FY2026", "chunk": NVDA_GOLD},
              {"ticker": "AMD", "period": "FY2025", "chunk": AMD_GOLD},
              {"ticker": "INTC", "period": "FY2025", "chunk": INTC_GOLD}],
    )


def test_chunk_filings_reads_ticker_and_period_from_the_graph(neo4j_driver):
    _structural(neo4j_driver)
    assert chunk_filings(neo4j_driver, [AMD_GOLD, "0000002488-26-000018:9999"]) == {
        AMD_GOLD: ChunkFiling("AMD", "FY2025")}


@pytest.fixture
def graph_env(neo4j_driver, monkeypatch):
    """The command's NEO4J_* settings pointed at the test database, with q0120's chunks."""
    _structural(neo4j_driver)
    for suffix in ("URI", "USER", "PASSWORD"):
        monkeypatch.setenv(f"NEO4J_{suffix}", os.environ[f"NEO4J_TEST_{suffix}"])


def _query_dir(tmp_path, chunk_ids) -> Path:
    queries = tmp_path / "queries"
    queries.mkdir()
    (queries / "q0120.cypher").write_text(f"RETURN {list(chunk_ids)!r} AS chunk_ids")
    return queries


def test_the_command_passes_and_writes_a_labelled_report(graph_env, tmp_path, capsys):
    queries = _query_dir(tmp_path, COMPETITORS.gold_chunk_ids)
    assert main([], query_dir=queries, report_dir=tmp_path) == EXIT_OK
    report = json.loads((tmp_path / "multihop-run1.json").read_text())
    assert (report["label"], report["passed"], report["total"]) == (LABEL, 1, 1)
    assert report["questions"][0]["returned_chunk_ids"] == sorted(COMPETITORS.gold_chunk_ids)
    assert "q0120: PASS" in capsys.readouterr().out


def test_the_command_exits_4_when_a_question_fails(graph_env, tmp_path, capsys):
    queries = _query_dir(tmp_path, [NVDA_GOLD, AMD_GOLD])
    assert main([], query_dir=queries, report_dir=tmp_path) == EXIT_VIOLATIONS
    assert "INTC MISS" in capsys.readouterr().out


def test_the_command_refuses_an_empty_query_folder(tmp_path, capsys):
    assert main([], query_dir=tmp_path, report_dir=tmp_path) == EXIT_RUN_ERROR
    assert "no *.cypher" in capsys.readouterr().err
    assert not list(tmp_path.glob("multihop-run*.json"))
