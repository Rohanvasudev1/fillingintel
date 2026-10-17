"""`python -m graph.global_provenance` on ``neo4j-test`` (Step 12 ticket 07).

The graph is ``tests/global_data.py``'s.  Each run file here is a small
graph_global run written by hand around that graph's real chunk IDs, with one
deliberately broken citation where a test needs one.  Needs ``NEO4J_TEST_URI``.
"""
import json
import os

import pytest

from graph.constraints import apply_constraints
from graph.global_provenance import LABEL, main
from graph.validate import EXIT_OK, EXIT_RUN_ERROR, EXIT_USAGE, EXIT_VIOLATIONS
from graph.write import write_batch
from ingest.chunker import chunk_filing
from tests.global_data import community_batch
from tests.graph_local_data import (
    K_BLACKWELL_CHUNK,
    K_RISK,
    Q_RISK,
    Q_RISK_CHUNK,
    graph_local_batch,
    key_of,
)
from tests.graph_test_data import OFFICERS_CHUNK, SUPPLY_CHUNK, TSMC

LOAD_RUN = "load-run7"


@pytest.fixture(scope="module")
def graph(neo4j_test_session, nvda_10k_filing, nvda_10q_filing, amd_10k_filing):
    from tests.neo4j_fixtures import wipe_except_marker

    texts, sections = {}, {}
    for filing in (nvda_10k_filing, nvda_10q_filing, amd_10k_filing):
        for c in chunk_filing(filing):
            texts[c.chunk_id] = filing.text[c.char_start:c.char_end]
            sections[c.chunk_id] = c.section
    wipe_except_marker(neo4j_test_session)
    apply_constraints(neo4j_test_session)
    write_batch(neo4j_test_session, community_batch(graph_local_batch(texts, sections)))
    return neo4j_test_session


@pytest.fixture
def graph_env(graph, monkeypatch, tmp_path):
    """The command's NEO4J_* settings pointed at the test graph, and a load report folder."""
    for suffix in ("URI", "USER", "PASSWORD"):
        monkeypatch.setenv(f"NEO4J_{suffix}", os.environ[f"NEO4J_TEST_{suffix}"])
    loads = tmp_path / "loads"
    loads.mkdir()
    (loads / f"{LOAD_RUN}.json").write_text("{}")
    return loads


def _question(qid: str, selected: list[str], kept: list[tuple[str, list[str]]],
              dropped: list[tuple[str, list[str]]] = (),
              points: dict[str, list[tuple[list[str], bool]]] | None = None) -> dict:
    """*points*: per community, its map points' citations and whether each reached reduce."""
    def point(citations: list[str], in_reduce: bool) -> dict:
        return {"text": "p", "score": 80, "citations": citations, "kept": True,
                "in_reduce": in_reduce}

    return {
        "id": qid,
        "answer": {"kept": [{"text": t, "citations": c} for t, c in kept],
                   "dropped": [{"text": t, "citations": c, "reason": "not_retrieved"}
                               for t, c in dropped]},
        "graph": {"global_search": {"map_calls": [
            {"community_id": c, "rank": i,
             "points": [point(*p) for p in (points or {}).get(c, [])]}
            for i, c in enumerate(selected, 1)]}},
    }


def _run_file(tmp_path, *questions: dict, arm: str = "graph_global",
              load_run: str = LOAD_RUN):
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"header": {"arm": arm, "graph": {"load_run": load_run}},
                                "questions": list(questions)}))
    return path


def _main(run, loads, out):
    return main([str(run)], load_reports=loads, report_dir=out)


def _report(out) -> dict:
    return json.loads((out / "global-provenance-run1.json").read_text())


def test_kept_citations_trace_to_a_member_and_the_report_holds_every_trace(
        graph_env, tmp_path, capsys):
    run = _run_file(tmp_path, _question(
        "q0030", ["lower-blackwell", "lower-supply"],
        kept=[("TSMC makes the wafers.", [SUPPLY_CHUNK]),
              ("Blackwell drives revenue.", [K_BLACKWELL_CHUNK])],
        dropped=[("An uncheckable claim.", [OFFICERS_CHUNK])]))
    assert _main(run, graph_env, tmp_path) == EXIT_OK
    report = _report(tmp_path)
    assert (report["label"], report["load_run"], report["citations"], report["breaks"]) == (
        LABEL, LOAD_RUN, 2, 0)
    supply = next(t for t in report["traces"] if t["chunk_id"] == SUPPLY_CHUNK)
    assert supply["found"] == {"community_id": "lower-supply", "via": "evidenced_by",
                               "node": key_of(TSMC)}
    assert "0 breaks" in capsys.readouterr().out


def test_one_broken_citation_exits_4_and_is_listed(graph_env, tmp_path, capsys):
    run = _run_file(tmp_path, _question(
        "q0023", ["lower-supply"],
        kept=[("TSMC makes the wafers.", [SUPPLY_CHUNK]),
              ("Huang leads NVIDIA.", [OFFICERS_CHUNK])]))  # Huang is in lower-lone
    assert _main(run, graph_env, tmp_path) == EXIT_VIOLATIONS
    report = _report(tmp_path)
    assert report["breaks"] == 1
    broken = [t for t in report["traces"] if t["found"] is None]
    assert [(b["id"], b["chunk_id"], b["selected"]) for b in broken] == [
        ("q0023", OFFICERS_CHUNK, ["lower-supply"])]
    assert "q0023" in capsys.readouterr().out


def test_a_top_community_reaches_its_lower_communities_members(graph_env, tmp_path):
    run = _run_file(tmp_path, _question("q1", ["top-a"], kept=[("Wafers.", [SUPPLY_CHUNK])]))
    assert _main(run, graph_env, tmp_path) == EXIT_OK
    assert _report(tmp_path)["traces"][0]["found"]["community_id"] == "top-a"


@pytest.fixture
def risk_without_its_evidence(graph):
    """Q_RISK's EVIDENCED_BY edge removed for one test, so only PERSISTS_AS cites its chunk."""
    key, chunk = key_of(Q_RISK), Q_RISK_CHUNK
    records, _, _ = graph.execute_query(
        "MATCH (:RiskFactor {key: $key})-[r:EVIDENCED_BY]->(:Chunk {chunk_id: $chunk}) "
        "WITH r, properties(r) AS props DELETE r RETURN props", key=key, chunk=chunk)
    yield
    graph.execute_query(
        "MATCH (n:RiskFactor {key: $key}), (c:Chunk {chunk_id: $chunk}) "
        "CREATE (n)-[r:EVIDENCED_BY]->(c) SET r = $props",
        key=key, chunk=chunk, props=records[0]["props"])


def test_an_edge_between_members_citing_the_chunk_traces_it(
        graph_env, risk_without_its_evidence, tmp_path):
    run = _run_file(tmp_path, _question("q2", ["top-b"], kept=[("It persisted.", [Q_RISK_CHUNK])]))
    assert _main(run, graph_env, tmp_path) == EXIT_OK
    assert _report(tmp_path)["traces"][0]["found"] == {
        "community_id": "top-b", "via": "edge", "type": "PERSISTS_AS",
        "start": key_of(K_RISK), "end": key_of(Q_RISK)}


def test_another_arms_run_is_a_bad_argument(graph_env, tmp_path, capsys):
    run = _run_file(tmp_path, arm="vector")
    assert _main(run, graph_env, tmp_path) == EXIT_USAGE
    assert "graph_global" in capsys.readouterr().err
    assert not list(tmp_path.glob("global-provenance-run*.json"))


def test_a_missing_run_file_is_a_bad_argument(graph_env, tmp_path):
    assert _main(tmp_path / "absent.json", graph_env, tmp_path) == EXIT_USAGE


def test_a_graph_rebuilt_since_the_run_cannot_be_checked(graph_env, tmp_path, capsys):
    (graph_env / "load-run8.json").write_text('{"counts": {"nodes": 1}}')
    run = _run_file(tmp_path, _question("q1", ["top-a"], kept=[("Wafers.", [SUPPLY_CHUNK])]))
    assert _main(run, graph_env, tmp_path) == EXIT_RUN_ERROR
    assert "load-run8" in capsys.readouterr().err
    assert not list(tmp_path.glob("global-provenance-run*.json"))


def test_a_selected_community_the_graph_lacks_cannot_be_checked(graph_env, tmp_path, capsys):
    run = _run_file(tmp_path, _question("q1", ["lower-gone"], kept=[("Wafers.", [SUPPLY_CHUNK])]))
    assert _main(run, graph_env, tmp_path) == EXIT_RUN_ERROR
    assert "lower-gone" in capsys.readouterr().err


def test_a_later_load_of_the_same_build_checks_the_run(graph_env, tmp_path):
    """A rebuild whose load report differs only in commit and write time (Step 13 ticket 01
    rebuilt the Step 12 graph to record its load run) is the graph the run read."""
    (graph_env / f"{LOAD_RUN}.json").write_text(
        '{"commit": "aaa1111", "write_seconds": 45.4, "counts": {"nodes": 1}}')
    (graph_env / "load-run8.json").write_text(
        '{"commit": "bbb2222", "write_seconds": 59.7, "counts": {"nodes": 1}}')
    run = _run_file(tmp_path, _question("q1", ["top-a"], kept=[("Wafers.", [SUPPLY_CHUNK])]))
    assert _main(run, graph_env, tmp_path) == EXIT_OK
    report = _report(tmp_path)
    assert (report["load_run"], report["checked_load_run"]) == (LOAD_RUN, "load-run8")


def test_the_strict_count_traces_through_the_citing_map_points_community(graph_env, tmp_path):
    # lower-supply holds the chunk, but only lower-blackwell's point cited it for reduce.
    run = _run_file(tmp_path, _question(
        "q0030", ["lower-blackwell", "lower-supply"],
        kept=[("TSMC makes the wafers.", [SUPPLY_CHUNK]),
              ("Blackwell drives revenue.", [K_BLACKWELL_CHUNK])],
        points={"lower-blackwell": [([SUPPLY_CHUNK, K_BLACKWELL_CHUNK], True)],
                "lower-supply": [([SUPPLY_CHUNK], False)]}))
    assert _main(run, graph_env, tmp_path) == EXIT_OK  # the gate is the existing count
    report = _report(tmp_path)
    assert report["breaks"] == 0
    strict = report["strict"]
    assert (strict["citations"], strict["breaks"]) == (2, 1)
    broken = [t for t in strict["traces"] if t["found"] is None]
    assert [(b["chunk_id"], b["citing_communities"]) for b in broken] == [
        (SUPPLY_CHUNK, ["lower-blackwell"])]
    held = next(t for t in strict["traces"] if t["chunk_id"] == K_BLACKWELL_CHUNK)
    assert held["found"]["community_id"] == "lower-blackwell"


def test_the_strict_count_passes_through_the_right_communitys_point(graph_env, tmp_path):
    run = _run_file(tmp_path, _question(
        "q0030", ["lower-blackwell", "lower-supply"],
        kept=[("TSMC makes the wafers.", [SUPPLY_CHUNK])],
        points={"lower-supply": [([SUPPLY_CHUNK], True)]}))
    assert _main(run, graph_env, tmp_path) == EXIT_OK
    strict = _report(tmp_path)["strict"]
    assert strict["breaks"] == 0
    assert strict["traces"][0]["found"]["community_id"] == "lower-supply"
