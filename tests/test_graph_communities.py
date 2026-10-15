"""The community graph and its Leiden communities (Step 12).

Batches are hand-built around the real chunk IDs and quotes of
tests/graph_test_data.py. One test runs over the whole valid test graph and
one over build_graph()'s output from fixture rows and an extract file.
"""
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from datetime import date

import pytest

from graph.batch import Batch, EdgeRecord, Evidence, NodeRecord, NodeRef
from graph.build_input import (
    EXTRACT_PROMPT_PREFIX,
    ChunkRow,
    ExtractEdge,
    ExtractFile,
    ExtractNode,
    FilingRow,
    StructuralRows,
)
from graph.builder import build_graph
from graph.community import (
    DEFAULT_SETTINGS,
    LOWER,
    TOP,
    find_communities,
    member_hash,
)
from graph.community_graph import DuplicateKey, community_graph
from ingest.provenance import REPO_ROOT
from tests.graph_test_data import (
    AMD_COMPETITION_CHUNK,
    CUSTOMERS_CHUNK,
    EXPORT_CHUNK,
    NVDA_10K,
    NVDA_CIK,
    OFFICERS_CHUNK,
    QUOTES,
    SEGMENTS_CHUNK,
    SUPPLY_CHUNK,
    TEST_PROMPT,
    edge_evidence,
)

NVIDIA = NodeRef.of("Company", cik="1045810")
CHUNKS = tuple(QUOTES)


def _node(label, key, *chunk_ids):
    evidence = tuple(Evidence(c, QUOTES[c], "stated", TEST_PROMPT) for c in chunk_ids)
    return NodeRecord(label, {"key": key, "name": key.split(":")[-1]}, evidence)


def _org(key, *chunk_ids):
    return _node("Organization", f"Organization:{key}", *chunk_ids)


def _ref(node):
    return NodeRef.of(node.label, key=node.properties["key"])


def _edge(type_, start, end, *chunk_ids):
    return EdgeRecord(type_, _ref(start) if isinstance(start, NodeRecord) else start,
                      _ref(end) if isinstance(end, NodeRecord) else end,
                      edge_evidence(*chunk_ids))


def _key(node):
    return node.properties["key"]


# ── community graph ────────────────────────────────────────────────────────────

def test_metric_values_and_structural_nodes_are_left_out():
    a, b = _org("a", SUPPLY_CHUNK), _org("b", SUPPLY_CHUNK)
    segment = _node("Segment", "Segment:1045810:compute", SEGMENTS_CHUNK)
    metric = _node("MetricValue", f"{NVDA_10K}:segment:revenue:usd:FY2026", SEGMENTS_CHUNK)
    batch = Batch(
        nodes=(NodeRecord("Company", {"cik": "1045810", "name": "NVIDIA CORP"}),
               NodeRecord("Chunk", {"chunk_id": SUPPLY_CHUNK, "accession_no": NVDA_10K,
                                    "section": "part_i_item_1a"}),
               a, b, segment, metric),
        edges=(_edge("SUPPLIES", a, NVIDIA, SUPPLY_CHUNK),
               _edge("HAS_SEGMENT", NVIDIA, segment, SEGMENTS_CHUNK),
               _edge("MEASURES", metric, segment, SEGMENTS_CHUNK)),
    )
    graph = community_graph(batch)
    assert dict(graph.labels) == {_key(a): "Organization", _key(b): "Organization"}
    assert dict(graph.weights) == {(_key(a), _key(b)): 1}
    assert graph.singletons == (_key(segment),)


def test_extracted_edges_weigh_their_evidence_and_co_occurrence_the_shared_chunks():
    a = _org("a", SUPPLY_CHUNK, EXPORT_CHUNK, SEGMENTS_CHUNK)
    b = _org("b", SUPPLY_CHUNK, EXPORT_CHUNK)
    batch = Batch(nodes=(a, b), edges=(
        _edge("SUPPLIES", a, b, SUPPLY_CHUNK),
        _edge("CUSTOMER_OF", b, a, OFFICERS_CHUNK, CUSTOMERS_CHUNK),  # the other direction
    ))
    graph = community_graph(batch)
    pair = (_key(a), _key(b))
    assert dict(graph.extracted) == {pair: 3}
    assert dict(graph.co_occurrence) == {pair: 2}
    assert dict(graph.weights) == {pair: 5}


def test_self_loops_are_dropped_and_nodes_with_no_edge_are_returned_sorted():
    a, z = _org("z", SUPPLY_CHUNK), _org("a", EXPORT_CHUNK)
    graph = community_graph(Batch(nodes=(a, z), edges=(_edge("OWNS", a, a, SUPPLY_CHUNK),)))
    assert dict(graph.weights) == {}
    assert graph.singletons == (_key(z), _key(a))


def test_a_key_held_by_two_nodes_is_refused():
    with pytest.raises(DuplicateKey, match="Organization:a"):
        community_graph(Batch(nodes=(_org("a", SUPPLY_CHUNK), _org("a", EXPORT_CHUNK))))


def test_the_input_hash_ignores_order_and_follows_every_evidence_chunk():
    a, b, c = (_org(k, SUPPLY_CHUNK) for k in "abc")
    edge = _edge("SUPPLIES", a, b, EXPORT_CHUNK)
    forward = community_graph(Batch(nodes=(a, b, c), edges=(edge,)))
    backward = community_graph(Batch(nodes=(c, b, a), edges=(edge,)))
    assert forward.input_sha256 == backward.input_sha256
    swapped = community_graph(Batch(nodes=(a, b, c),
                                    edges=(_edge("SUPPLIES", a, b, OFFICERS_CHUNK),)))
    assert dict(swapped.weights) == dict(forward.weights)  # same count, other chunk
    assert swapped.input_sha256 != forward.input_sha256
    moved = community_graph(Batch(nodes=(a, b, _org("c", EXPORT_CHUNK)), edges=(edge,)))
    assert moved.input_sha256 != forward.input_sha256


# ── communities ────────────────────────────────────────────────────────────────

def _cliques(*sizes):
    """Clusters of organizations, each joined by one chunk of its own (co-occurrence)."""
    assert len(sizes) <= len(CHUNKS)
    return tuple(_org(f"c{i}-m{j:02d}", CHUNKS[i])
                 for i, size in enumerate(sizes) for j in range(size))


def _by_level(communities, level):
    return [c for c in communities if c.level == level]


def test_same_graph_and_seed_give_identical_communities_whatever_the_input_order():
    nodes = _cliques(4, 3, 3)
    bridge = _edge("SUPPLIES", nodes[0], nodes[4], AMD_COMPETITION_CHUNK)
    first = find_communities(community_graph(Batch(nodes=nodes, edges=(bridge,))))
    again = find_communities(community_graph(Batch(nodes=nodes, edges=(bridge,))))
    shuffled = find_communities(community_graph(Batch(nodes=nodes[::-1], edges=(bridge,))))
    assert first == again == shuffled
    assert sorted(len(c.members) for c in _by_level(first, TOP)) == [3, 3, 4]


def test_member_hashes_cover_the_sorted_member_keys():
    nodes = _cliques(3)
    (top, lower) = find_communities(community_graph(Batch(nodes=nodes[::-1])))
    keys = sorted(_key(n) for n in nodes)
    assert top.members == lower.members == tuple(keys)
    assert top.member_hash == member_hash(reversed(keys)) == hashlib.sha256(
        json.dumps(keys, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    assert (top.community_id, lower.community_id) == (
        f"top-{top.member_hash[:12]}", f"lower-{top.member_hash[:12]}")


def test_a_top_community_over_25_members_is_split_and_a_smaller_one_is_its_own_lower():
    big, small = 30, 3
    communities = find_communities(community_graph(Batch(nodes=_cliques(big, small))))
    tops = {len(c.members): c for c in _by_level(communities, TOP)}
    assert set(tops) == {big, small}
    lowers = _by_level(communities, LOWER)
    assert all(c.parent in {t.community_id for t in tops.values()} for c in lowers)
    assert [c.members for c in lowers if c.parent == tops[small].community_id] == [
        tops[small].members]
    under_big = [c for c in lowers if c.parent == tops[big].community_id]
    assert sorted(m for c in under_big for m in c.members) == list(tops[big].members)
    assert all(c.parent is None for c in tops.values())


def test_communities_under_three_members_are_not_reportable():
    communities = find_communities(community_graph(Batch(nodes=_cliques(2, 3))))
    assert {(c.level, len(c.members), c.reportable) for c in communities} == {
        (TOP, 2, False), (LOWER, 2, False), (TOP, 3, True), (LOWER, 3, True)}


def test_a_graph_with_no_edge_has_no_communities():
    assert find_communities(community_graph(Batch(nodes=(_org("a", SUPPLY_CHUNK),)))) == ()


def test_the_settings_hash_follows_every_setting():
    assert DEFAULT_SETTINGS.sha256() == DEFAULT_SETTINGS.sha256()
    for change in ({"resolution": 0.5}, {"randomness": 0.01}, {"iterations": 2},
                   {"seed": 1}, {"split_over": 24}, {"min_report_members": 2}):
        assert replace(DEFAULT_SETTINGS, **change).sha256() != DEFAULT_SETTINGS.sha256()
    with pytest.raises(AttributeError):
        DEFAULT_SETTINGS.seed = 1  # type: ignore[misc]


def test_the_valid_test_graph_gives_one_unreportable_pair(graph_test_batch):
    graph = community_graph(graph_test_batch)
    compute = "Segment:1045810:compute & networking"
    data_center = "MarketPlatform:1045810:data center"
    assert dict(graph.weights) == {(data_center, compute): 1}
    assert graph.singletons == ("org:tsmc", "person:jen-hsun-huang",
                                "regulation:us-export-controls")
    top, lower = find_communities(graph)
    assert (top.level, lower.level, lower.parent) == (TOP, LOWER, top.community_id)
    assert top.members == lower.members == (data_center, compute)
    assert not top.reportable


def test_the_builder_output_maps_aliases_and_drops_filer_ends():
    rows = StructuralRows(
        filings=(FilingRow(NVDA_10K, NVDA_CIK, "NVIDIA CORP", "10-K", "FY2026",
                           date(2026, 2, 26)),),
        chunks=tuple(ChunkRow(c, NVDA_10K, "part_i_item_1", 0, 1) for c in QUOTES
                     if c.startswith(NVDA_10K)),
        risk_sections=(),
    )
    prompt = f"{EXTRACT_PROMPT_PREFIX}34a82e2b"

    def ev(chunk_id):
        return Evidence(chunk_id, QUOTES[chunk_id], "stated", prompt)

    def extracted(label, key, *chunk_ids):
        return ExtractNode(label, {"key": key, "name": key.split(":")[-1]},
                           tuple(ev(c) for c in chunk_ids))

    tsmc, filer = "Organization:tsmc", "Organization:nvidia corporation"
    fab, rule = "Facility:fab 21", "Regulation:export controls"
    nodes = (extracted("Organization", tsmc, SUPPLY_CHUNK),
             extracted("Organization", filer, SUPPLY_CHUNK),
             extracted("Facility", fab, EXPORT_CHUNK),
             extracted("Regulation", rule, SUPPLY_CHUNK, EXPORT_CHUNK))
    edges = (ExtractEdge("SUPPLIES", NodeRef.of("Organization", key=tsmc),
                         NodeRef.of("Organization", key=filer), None, None, (ev(SUPPLY_CHUNK),)),
             ExtractEdge("OWNS", NodeRef.of("Organization", key=tsmc),
                         NodeRef.of("Facility", key=fab), None, None, (ev(EXPORT_CHUNK),)))
    built = build_graph(rows, [ExtractFile(NVDA_10K, 1, "0" * 64, nodes, edges)],
                        {filer: f"Company:{NVDA_CIK}"}, ())
    graph = community_graph(built.batch)
    assert dict(graph.labels) == {fab: "Facility", tsmc: "Organization", rule: "Regulation"}
    assert dict(graph.weights) == {(fab, tsmc): 1, (fab, rule): 1, (tsmc, rule): 1}
    assert graph.singletons == ()
    top, lower = find_communities(graph)
    assert top.members == lower.members == (fab, tsmc, rule)
    assert top.reportable and lower.reportable


_NO_NETWORK_SCRIPT = """
import socket

def _blocked(*args, **kwargs):
    raise RuntimeError("network access attempted")

socket.socket = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked

import graspologic_native
_, found = graspologic_native.leiden(edges=[("a", "b", 1.0), ("b", "c", 1.0)], seed=1)
assert set(found) == {"a", "b", "c"}
print("ok")
"""


def test_graspologic_native_imports_and_runs_with_the_network_blocked():
    result = subprocess.run([sys.executable, "-c", _NO_NETWORK_SCRIPT], capture_output=True,
                            text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


# ── python -m graph.communities ────────────────────────────────────────────────

def _inputs():
    from graph.load import LoadInputs

    return LoadInputs(files=(), alias_map={}, alias_map_sha256="a" * 64, gaps={}, links=(),
                      links_sha256="b" * 64, extracted_under=(3,))


def _built(batch):
    from graph.builder import BodyReport, BuildReport, Built

    return Built(batch, BuildReport((), (), (), (), BodyReport(0, (), (), ())))


def test_the_command_writes_the_same_run_file_twice_apart_from_run_metadata(tmp_path):
    from graph.communities import main
    from graph.validate import EXIT_OK

    nodes = _cliques(30, 3, 2)
    batch = Batch(nodes=(*nodes, _org("alone", AMD_COMPETITION_CHUNK)))
    read = lambda: (_inputs(), _built(batch))  # noqa: E731
    assert main([], out_dir=tmp_path, read=read) == EXIT_OK
    assert main([], out_dir=tmp_path, read=read) == EXIT_OK
    first, second = (json.loads((tmp_path / f"communities-run{n}.json").read_text())
                     for n in (1, 2))
    assert {k: v for k, v in first.items() if k != "commit"} == {
        k: v for k, v in second.items() if k != "commit"}
    assert first["settings"] == DEFAULT_SETTINGS.to_dict()
    assert first["settings_sha256"] == DEFAULT_SETTINGS.sha256()
    assert first["library"] == {"name": "graspologic-native", "version": "1.3.1"}
    assert first["community_graph"]["input_sha256"] == community_graph(batch).input_sha256
    assert first["community_graph"]["nodes"] == 35
    assert first["singletons"] == ["Organization:alone"]
    assert first["levels"]["top"]["count"] == 3
    assert first["levels"]["top"]["sizes"] == sorted(first["levels"]["top"]["sizes"],
                                                     reverse=True)
    small = [c for c in first["communities"] if len(c["members"]) == 2]
    assert sorted(first["unreportable"]) == sorted(c["community_id"] for c in small)
    assert {c["level"] for c in first["communities"]} == {TOP, LOWER}


def test_the_command_cannot_run_without_database_url(tmp_path, monkeypatch):
    from graph.communities import main
    from graph.validate import EXIT_RUN_ERROR

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main([], out_dir=tmp_path) == EXIT_RUN_ERROR
    assert list(tmp_path.iterdir()) == []


def test_the_command_refuses_unknown_arguments(tmp_path):
    from graph.communities import main
    from graph.validate import EXIT_USAGE

    with pytest.raises(SystemExit) as exit_:
        main(["--seed", "1"], out_dir=tmp_path)
    assert exit_.value.code == EXIT_USAGE
