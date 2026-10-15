"""graph_global's retrieval on ``neo4j-test`` and a throwaway Postgres schema
(Step 12 ticket 05).

The graph is ``tests/graph_local_data.py``'s, around real fixture chunk IDs,
with four lower communities and two top ones written over it.  Report vectors
and chunk vectors are fakes whose cosine with the query (the first axis) is the
similarity each test states, so the expected order follows from the rule.
Needs ``DATABASE_URL`` and ``NEO4J_TEST_URI``.
"""
import hashlib
import json
from dataclasses import replace
from types import MappingProxyType

import pytest

from graph.batch import Batch, EdgeRecord, NodeRecord, NodeRef
from graph.community import LOWER, TOP, member_hash
from graph.constraints import apply_constraints
from graph.report_file import ReportEntry, ReportsFile
from graph.write import write_batch
from ingest.chunker import chunk_filing
from ingest.store import load_filing, save_embedding
from ingest.voyage import BatchEmbedding
from retrieve.arm import ArmError
from retrieve.global_retrieval import GlobalSelection, loaded_reports_file
from retrieve.query_cache import CachedBatchEmbedder
from tests.conftest import FIXTURE_NAMES, throwaway_schema
from tests.graph_local_data import (
    BLACKWELL,
    K_BLACKWELL_CHUNK,
    K_RISK,
    K_RISK_CHUNK,
    Q_BLACKWELL_CHUNK,
    Q_RISK,
    Q_RISK_CHUNK,
    QUERY_VECTOR,
    at_similarity,
    graph_local_batch,
    key_of,
)
from tests.graph_test_data import (
    COMPUTE,
    DATA_CENTER,
    EXPORT_CHUNK,
    EXPORT_CONTROLS,
    HUANG,
    OFFICERS_CHUNK,
    SEGMENTS_CHUNK,
    SUPPLY_CHUNK,
    TEST_REPORT_PROMPT,
    TSMC,
)
from tests.vector_fakes import MODEL, FakeQueryEmbedder

LOADED = ("nvda_10k", "nvda_10q", "amd_10k")
UNFILTERED = "What are the main supply chain risks?"
NVDA_FY2026 = "What did NVIDIA say about supply in fiscal 2026?"  # the 10-K only
AMD_FY2025 = "What did AMD's fiscal 2025 10-K say about competition?"  # no community

MEMBERS = {
    "lower-supply": (TSMC, COMPUTE, DATA_CENTER),  # 10-K chunks 0006, 0012
    "lower-blackwell": (BLACKWELL, Q_RISK),  # 10-K 0061; 10-Q 0035, 0057
    "lower-export": (EXPORT_CONTROLS, K_RISK),  # 10-K 0013, 0041
    "lower-lone": (HUANG,),  # no report
}
CHILDREN = {"top-a": ("lower-supply", "lower-lone"),
            "top-b": ("lower-blackwell", "lower-export")}
REPORTED = ("lower-supply", "lower-blackwell", "lower-export", "top-a", "top-b")
CHUNK_SIMILARITIES = {
    SUPPLY_CHUNK: 0.7, SEGMENTS_CHUNK: 0.6, K_BLACKWELL_CHUNK: 0.5, Q_BLACKWELL_CHUNK: 0.8,
    Q_RISK_CHUNK: 0.4, EXPORT_CHUNK: 0.3, K_RISK_CHUNK: 0.9, OFFICERS_CHUNK: 0.2,
}
SIMILARITIES = {"lower-supply": 0.5, "lower-blackwell": 0.9, "lower-export": 0.7,
                "top-a": 0.8, "top-b": 0.6}


def _keys(community_id: str) -> list[str]:
    refs = MEMBERS.get(community_id) or [m for c in CHILDREN[community_id] for m in MEMBERS[c]]
    return [key_of(r) for r in refs]


def _summary(community_id: str) -> str:
    return f"The {community_id} community."


def _text(community_id: str) -> str:
    return f"Title {community_id}: {_summary(community_id)}"


def _community(community_id: str) -> NodeRecord:
    level = TOP if community_id in CHILDREN else LOWER
    props = {"community_id": community_id, "level": level,
             "member_hash": member_hash(_keys(community_id)), "report_chunk_ids": []}
    if community_id in REPORTED:
        props |= {"title": f"Title {community_id}", "report": _summary(community_id),
                  "report_prompt": TEST_REPORT_PROMPT}
    return NodeRecord("Community", props)


def _ref(community_id: str) -> NodeRef:
    return NodeRef.of("Community", community_id=community_id)


def community_batch(base: Batch) -> Batch:
    nodes = tuple(_community(c) for c in (*MEMBERS, *CHILDREN))
    edges = (
        *(EdgeRecord("IN_COMMUNITY", m, _ref(c)) for c, ms in MEMBERS.items() for m in ms),
        *(EdgeRecord("PARENT_OF", _ref(t), _ref(c)) for t, cs in CHILDREN.items() for c in cs),
    )
    return Batch(nodes=(*base.nodes, *nodes), edges=(*base.edges, *edges))


def reports_file(**changed: dict) -> ReportsFile:
    entries = {}
    for community_id in REPORTED:
        level = TOP if community_id in CHILDREN else LOWER
        fields = {"member_hash": member_hash(_keys(community_id)),
                  "title": f"Title {community_id}", "summary": _summary(community_id),
                  **changed.get(community_id, {})}
        entries[community_id] = ReportEntry(
            community_id, fields["member_hash"], level, fields["title"], fields["summary"], (),
            TEST_REPORT_PROMPT, fields["summary"])
    return ReportsFile("reports-run1.jsonl", "r" * 64, "communities-run1.json", "c" * 64,
                       "a" * 64, True, MappingProxyType(entries))


class FakeReportEmbedder:
    def __init__(self, similarities):
        self._by_text = {_text(c): s for c, s in similarities.items()}

    def embed_batch(self, texts, model, input_type) -> BatchEmbedding:
        return BatchEmbedding(vectors=tuple(at_similarity(self._by_text[t]) for t in texts),
                              total_tokens=5 * len(texts))


@pytest.fixture(scope="module")
def chunks(nvda_10k_filing, nvda_10q_filing, amd_10k_filing):
    texts, sections = {}, {}
    for filing in (nvda_10k_filing, nvda_10q_filing, amd_10k_filing):
        for c in chunk_filing(filing):
            texts[c.chunk_id] = filing.text[c.char_start:c.char_end]
            sections[c.chunk_id] = c.section
    return texts, sections


@pytest.fixture(scope="module")
def corpus(fixture_records, chunks):
    """Postgres holding the three filings, with fake vectors for the community chunks."""
    texts, _ = chunks
    with throwaway_schema() as conn:
        for name in LOADED:
            record = fixture_records[FIXTURE_NAMES.index(name)]
            load_filing(conn, record, chunk_filing(record.filing))
        for chunk_id, similarity in CHUNK_SIMILARITIES.items():
            save_embedding(conn, chunk_id, MODEL, texts[chunk_id], at_similarity(similarity), 9)
        yield conn


@pytest.fixture
def graph(neo4j_driver, chunks):
    apply_constraints(neo4j_driver)
    write_batch(neo4j_driver, community_batch(graph_local_batch(*chunks)))
    return neo4j_driver


def _load_reports(tmp_path):
    directory = tmp_path / "load"
    directory.mkdir(exist_ok=True)
    (directory / "load-run5.json").write_text("{}", encoding="utf-8")
    return directory


def _cache(tmp_path, similarities=SIMILARITIES) -> CachedBatchEmbedder:
    cache = CachedBatchEmbedder(FakeReportEmbedder(similarities), cache_dir=tmp_path / "reports")
    cache.embed([_text(c) for c in similarities], MODEL, "document")
    return cache


def _selection(corpus, graph, tmp_path, *, cache=None, reports=None, **kwargs):
    return GlobalSelection(corpus, graph, FakeQueryEmbedder(QUERY_VECTOR),
                           cache or _cache(tmp_path), reports or reports_file(),
                           load_reports=_load_reports(tmp_path), **kwargs)


def _shown(found) -> dict[str, list[str]]:
    return {c.community_id: [k.chunk_id for k in c.chunks] for c in found.communities}


def test_lower_communities_with_reports_rank_by_cosine_with_chunks_by_cosine(
        corpus, graph, tmp_path):
    found = _selection(corpus, graph, tmp_path, n=4).retrieve(UNFILTERED)
    # lower-lone has no report and is never selected.
    assert _shown(found) == {
        "lower-blackwell": [Q_BLACKWELL_CHUNK, K_BLACKWELL_CHUNK, Q_RISK_CHUNK],
        "lower-export": [K_RISK_CHUNK, EXPORT_CHUNK],
        "lower-supply": [SUPPLY_CHUNK, SEGMENTS_CHUNK],
    }
    assert [c.similarity for c in found.communities] == pytest.approx([0.9, 0.7, 0.5])
    assert [c.rank for c in found.communities] == [1, 2, 3]
    assert [r.chunk_id for r in found.retrieval.retrieved] == [
        Q_BLACKWELL_CHUNK, K_BLACKWELL_CHUNK, Q_RISK_CHUNK, K_RISK_CHUNK, EXPORT_CHUNK,
        SUPPLY_CHUNK, SEGMENTS_CHUNK]
    assert [r.score for r in found.retrieval.retrieved][:2] == pytest.approx([0.8, 0.5])


def test_n_and_the_per_community_limit_cut_the_selection(corpus, graph, tmp_path):
    found = _selection(corpus, graph, tmp_path, n=1, per_community=2).retrieve(UNFILTERED)
    assert _shown(found) == {"lower-blackwell": [Q_BLACKWELL_CHUNK, K_BLACKWELL_CHUNK]}


def test_equal_report_similarities_tie_by_community_id(corpus, graph, tmp_path):
    tied = {**SIMILARITIES, "lower-supply": 0.9, "lower-export": 0.9}
    found = _selection(corpus, graph, tmp_path, cache=_cache(tmp_path, tied)).retrieve(UNFILTERED)
    assert list(_shown(found)) == ["lower-blackwell", "lower-export", "lower-supply"]


def test_the_filter_drops_other_periods_chunks(corpus, graph, tmp_path):
    found = _selection(corpus, graph, tmp_path).retrieve(NVDA_FY2026)
    # The 10-Q's chunks of lower-blackwell are outside fiscal 2026.
    assert _shown(found)["lower-blackwell"] == [K_BLACKWELL_CHUNK]
    assert Q_BLACKWELL_CHUNK not in [r.chunk_id for r in found.retrieval.retrieved]


def test_the_filter_drops_other_companies_communities(corpus, graph, tmp_path):
    found = _selection(corpus, graph, tmp_path).retrieve(AMD_FY2025)
    assert found.communities == ()
    assert found.retrieval.retrieved == ()
    assert found.retrieval.question_filter.accession_nos is not None


def test_the_top_level_reaches_evidence_through_its_children(corpus, graph, tmp_path):
    found = _selection(corpus, graph, tmp_path, level=TOP).retrieve(UNFILTERED)
    assert _shown(found) == {
        "top-a": [SUPPLY_CHUNK, SEGMENTS_CHUNK, OFFICERS_CHUNK],  # lower-lone's Huang too
        "top-b": [K_RISK_CHUNK, Q_BLACKWELL_CHUNK, K_BLACKWELL_CHUNK, Q_RISK_CHUNK,
                  EXPORT_CHUNK],
    }


def test_an_uncached_report_text_is_refused_at_start(corpus, graph, tmp_path):
    cache = _cache(tmp_path, {c: s for c, s in SIMILARITIES.items() if c != "lower-export"})
    with pytest.raises(ArmError, match="report texts have no cached"):
        _selection(corpus, graph, tmp_path, cache=cache)


def test_a_graph_community_whose_report_has_another_member_hash_is_refused(
        corpus, graph, tmp_path):
    stale = reports_file(**{"lower-supply": {"member_hash": "f" * 64}})
    with pytest.raises(ArmError, match="lower-supply"):
        _selection(corpus, graph, tmp_path, reports=stale)


def test_a_report_rewritten_since_the_graph_was_loaded_is_refused(corpus, graph, tmp_path):
    rewritten = reports_file(**{"lower-export": {"summary": "A newer summary."}})
    with pytest.raises(ArmError, match="lower-export"):
        _selection(corpus, graph, tmp_path, reports=rewritten)


def test_a_graph_community_with_no_report_in_the_file_is_refused(corpus, graph, tmp_path):
    reports = reports_file()
    missing = replace(reports, entries=MappingProxyType(
        {k: v for k, v in reports.entries.items() if k != "lower-export"}))
    with pytest.raises(ArmError, match="lower-export"):
        _selection(corpus, graph, tmp_path, reports=missing)


def test_a_chunk_with_no_vector_stops_the_question(corpus, graph, tmp_path, chunks):
    corpus.execute("DELETE FROM chunk_embeddings WHERE chunk_id = %s", (EXPORT_CHUNK,))
    corpus.commit()
    try:
        with pytest.raises(ArmError, match=EXPORT_CHUNK):
            _selection(corpus, graph, tmp_path).retrieve(UNFILTERED)
    finally:
        save_embedding(corpus, EXPORT_CHUNK, MODEL, chunks[0][EXPORT_CHUNK],
                       at_similarity(CHUNK_SIMILARITIES[EXPORT_CHUNK]), 9)


# ── the reports file the graph was loaded from ───────────────────────────────

def _reports_dir(tmp_path, report_line: dict):
    directory = tmp_path / "communities"
    directory.mkdir()
    header = {"kind": "header", "complete": True,
              "communities": {"file": "communities-run1.json", "sha256": "c" * 64,
                              "community_graph_input_sha256": "a" * 64}}
    raw = "".join(json.dumps(line) + "\n" for line in (header, report_line)).encode()
    (directory / "reports-run2.jsonl").write_bytes(raw)
    (directory / "reports-run3.jsonl").write_bytes(raw + b"\n")  # newer, not loaded
    return directory, hashlib.sha256(raw).hexdigest()


REPORT_LINE = {"kind": "report", "community_id": "lower-x", "member_hash": "b" * 64,
               "level": "lower", "title": "T", "summary": "S [c:0001].",
               "report": "S [c:0001].", "report_chunk_ids": ["c:0001"],
               "prompt_version": TEST_REPORT_PROMPT}


def _load_dir(tmp_path, sha: str):
    directory = tmp_path / "graph"
    directory.mkdir()
    (directory / "load-run4.json").write_text(json.dumps({"communities": None}))
    (directory / "load-run5.json").write_text(json.dumps({"communities": {
        "reports_file": "reports-run2.jsonl", "reports_sha256": sha}}))
    return directory


def test_the_reports_file_is_the_one_the_latest_load_names(tmp_path):
    communities, sha = _reports_dir(tmp_path, REPORT_LINE)
    found = loaded_reports_file(_load_dir(tmp_path, sha), communities)
    assert found.name == "reports-run2.jsonl"
    assert found.entries["lower-x"].summary == "S [c:0001]."


def test_a_reports_file_changed_since_the_load_is_refused(tmp_path):
    communities, _ = _reports_dir(tmp_path, REPORT_LINE)
    with pytest.raises(ArmError, match="changed since"):
        loaded_reports_file(_load_dir(tmp_path, "0" * 64), communities)


def test_a_load_without_communities_is_refused(tmp_path):
    communities, _ = _reports_dir(tmp_path, REPORT_LINE)
    directory = tmp_path / "graph"
    directory.mkdir()
    (directory / "load-run4.json").write_text(json.dumps({"communities": None}))
    with pytest.raises(ArmError, match="no communities"):
        loaded_reports_file(directory, communities)
