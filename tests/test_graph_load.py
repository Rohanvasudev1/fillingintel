"""Rebuilding the graph from the fixture filings in the Neo4j test instance (Step 10;
communities since Step 12).

The NVIDIA FY2026 and AMD FY2025 10-K fixtures give the structural rows (every
chunk the chunker makes); extract files written by extraction's own writer give
the extracted records. A rebuild deletes every node but :GraphMeta, so the
`unmarked` fixture removes the test marker first and puts it back after.
Skips without NEO4J_TEST_URI.
"""
from collections import Counter
from dataclasses import replace
from types import MappingProxyType

import pytest

from extract.pipeline import ExtractionRun
from extract.report import candidate_lines, write_candidates
from graph.batch import Batch, EdgeRecord, NodeRecord, NodeRef
from graph.build_input import ChunkRow, FilingRow, InputError, StructuralRows
from graph.builder import build_graph
from graph.community import find_communities
from graph.community_file import CommunitiesFile
from graph.community_graph import community_graph
from graph.community_load import CommunityInputs, CommunityMismatch
from graph.connection import DATABASE
from graph.constraints import GRAPH_META, GraphMetaMismatch, apply_constraints
from graph.load import load_graph, read_inputs
from graph.load_report import load_report
from graph.ontology import LABELS_BY_NAME, ONTOLOGY_VERSION, Kind
from graph.report_file import ReportEntry, ReportsFile
from graph.schema_text import SCHEMA_TEXT_SHA256
from graph.validate import validate_graph
from graph.write import BatchRejected, GraphNotEmpty, rebuild_graph
from persist.decisions import LinkEvidence
from persist.links import EXACT_TITLE, PersistLink
from persist.paths import write_links
from tests.graph_test_data import (
    AMD_10K,
    EXPORT_CHUNK,
    NVDA_10K,
    QUOTES,
    SUPPLY_CHUNK,
    TEST_REPORT_PROMPT,
    evidence,
)
from tests.neo4j_fixtures import MARKER_LABEL

PROMPT = "extract/v2@34a82e2b"
SUPPLY_RISK = f"{NVDA_10K}:supply"
EXPORT_RISK = f"{NVDA_10K}:export controls"
LINK = PersistLink(SUPPLY_RISK, EXPORT_RISK, EXACT_TITLE,
                   LinkEvidence(SUPPLY_CHUNK, QUOTES[SUPPLY_CHUNK]),
                   LinkEvidence(EXPORT_CHUNK, QUOTES[EXPORT_CHUNK]))


def _run(driver, query):
    records, _, _ = driver.execute_query(query, database_=DATABASE)
    return [r.data() for r in records]


@pytest.fixture
def unmarked(neo4j_driver):
    """The wiped test database with constraints and :GraphMeta but no marker."""
    apply_constraints(neo4j_driver)
    _run(neo4j_driver, f"MATCH (m:`{MARKER_LABEL}`) DELETE m")
    yield neo4j_driver
    _run(neo4j_driver, f"MERGE (:`{MARKER_LABEL}`)")


@pytest.fixture
def rows(nvda_10k_meta, amd_10k_meta, test_graph_chunks):
    return StructuralRows(
        filings=tuple(FilingRow(m.accession_no, m.cik, m.company_name, m.form_type,
                                m.fiscal_period, m.filing_date)
                      for m in (nvda_10k_meta, amd_10k_meta)),
        chunks=tuple(ChunkRow(c, c.split(":")[0], section, 0, 1)
                     for c, (section, _) in sorted(test_graph_chunks.items())),
        risk_sections=(),  # body evidence is tested in test_graph_bodies.py
    )


@pytest.fixture
def inputs(tmp_path, graph_test_batch):
    """Extract files for both fixture 10-Ks, an alias map, the extraction reports and a
    link file joining two NVIDIA risk factors."""
    extract_dir, reports_dir = tmp_path / "extract", tmp_path / "extraction"
    reports_dir.mkdir()
    for accession_no in (NVDA_10K, AMD_10K):
        nodes = tuple(_v2(n) for n in graph_test_batch.nodes
                      if LABELS_BY_NAME[n.label].kind is Kind.EXTRACTED
                      and n.evidence[0].chunk_id.startswith(accession_no))
        if accession_no == NVDA_10K:
            nodes = (*nodes, *(_risk(key, chunk) for key, chunk in
                               ((SUPPLY_RISK, SUPPLY_CHUNK), (EXPORT_RISK, EXPORT_CHUNK))))
        edges = tuple(_v2_edge(e) for e in graph_test_batch.edges if "chunk_ids" in e.properties
                      and e.properties["chunk_ids"][0].startswith(accession_no))
        run = ExtractionRun(accession_no, PROMPT, nodes, edges, (), (), (), (), (), ())
        write_candidates(extract_dir / f"{accession_no}-run2.jsonl", candidate_lines(run))
        (reports_dir / f"{accession_no}-run2.json").write_text(
            '{"ontology_version": 3, '
            '"failed_chunks": [{"chunk_id": "' + accession_no + ':0001", "reason": "x"}]}')
    alias_map = tmp_path / "alias_map.json"
    alias_map.write_text('{"org:tsmc": "org:tsmc"}')
    links = tmp_path / "links.jsonl"
    write_links(links, [LINK])
    return read_inputs(extract_dir, alias_map, reports_dir, links)


def _risk(key, chunk_id):
    return NodeRecord("RiskFactor", {"key": key, "title": key.split(":")[-1]},
                      (evidence(chunk_id, prompt=PROMPT),))


def _v2(node):
    return replace(node, evidence=tuple(replace(e, extract_prompt=PROMPT) for e in node.evidence))


def _v2_edge(edge):
    count = len(edge.properties["chunk_ids"])
    return replace(edge, properties={**edge.properties, "extract_prompts": [PROMPT] * count})


def _counts(driver):
    rows = _run(driver, f"MATCH (n) WHERE NOT n:`{GRAPH_META}` "
                        "RETURN labels(n)[0] AS label, count(*) AS n")
    return Counter({r["label"]: r["n"] for r in rows})


def test_the_fixture_filings_load_and_validate_with_zero_violations(
    unmarked, rows, inputs, test_graph_chunks,
):
    result = load_graph(unmarked, rows, inputs, rebuild=False, communities=None)
    assert result.validation.ok and result.validation.violation_count == 0
    counts = _counts(unmarked)
    assert counts["Chunk"] == len(test_graph_chunks)
    assert (counts["Company"], counts["Filing"], counts["Period"]) == (2, 2, 2)
    assert counts["Organization"] == 1 and counts["MetricValue"] == 1
    persists = _run(unmarked, "MATCH (a)-[e:PERSISTS_AS]->(b) "
                              "RETURN a.key AS a, b.key AS b, e.chunk_ids AS chunks")
    assert persists == [{"a": SUPPLY_RISK, "b": EXPORT_RISK,
                         "chunks": [SUPPLY_CHUNK, EXPORT_CHUNK]}]
    assert inputs.gaps == {NVDA_10K: (f"{NVDA_10K}:0001",), AMD_10K: (f"{AMD_10K}:0001",)}


def test_a_graph_with_data_is_refused_without_rebuild_and_replaced_with_it(
    unmarked, rows, inputs,
):
    load_graph(unmarked, rows, inputs, rebuild=False, communities=None)
    first = _counts(unmarked)
    with pytest.raises(GraphNotEmpty):
        load_graph(unmarked, rows, inputs, rebuild=False, communities=None)
    load_graph(unmarked, rows, inputs, rebuild=True, communities=None)
    assert _counts(unmarked) == first


def test_a_rejected_rebuild_leaves_the_previous_graph(unmarked, rows, inputs):
    result = load_graph(unmarked, rows, inputs, rebuild=False, communities=None)
    before = _counts(unmarked)
    bad = Batch(nodes=(*result.built.batch.nodes, NodeRecord("Organization", {"key": "x"})),
                edges=result.built.batch.edges)
    with pytest.raises(BatchRejected):
        rebuild_graph(unmarked, bad)
    assert _counts(unmarked) == before


def test_graph_meta_survives_a_rebuild(unmarked, rows, inputs):
    load_graph(unmarked, rows, inputs, rebuild=False, communities=None)
    load_graph(unmarked, rows, inputs, rebuild=True, communities=None)
    versions = _run(unmarked, f"MATCH (m:`{GRAPH_META}`) RETURN m.ontology_version AS v")
    assert versions == [{"v": ONTOLOGY_VERSION}]


def test_a_validation_that_cannot_run_after_the_write_is_reported_not_hidden(
    unmarked, rows, inputs, monkeypatch,
):
    from neo4j.exceptions import ServiceUnavailable

    import graph.load

    def unreachable(*_args, **_kwargs):
        raise ServiceUnavailable("gone")

    monkeypatch.setattr(graph.load, "validate_graph", unreachable)
    result = load_graph(unmarked, rows, inputs, rebuild=False, communities=None)
    assert result.validation is None
    assert "ServiceUnavailable" in result.validation_error
    assert _counts(unmarked)["Filing"] == 2  # the write stands


def test_a_missing_link_file_is_an_input_error(inputs, tmp_path):
    (tmp_path / "links.jsonl").unlink()
    with pytest.raises(InputError, match="links.jsonl"):
        read_inputs(tmp_path / "extract", tmp_path / "alias_map.json", tmp_path / "extraction",
                    tmp_path / "links.jsonl")


def test_the_load_report_records_the_link_files_hash_and_links_by_method(
    unmarked, rows, inputs, tmp_path,
):
    import hashlib

    result = load_graph(unmarked, rows, inputs, rebuild=False, communities=None)
    report = load_report("abc123", inputs, result)
    digest = hashlib.sha256((tmp_path / "links.jsonl").read_bytes()).hexdigest()
    assert report["persistence_links"] == {"sha256": digest, "by_method": {EXACT_TITLE: 1}}
    assert report["counts"]["edges"]["PERSISTS_AS"] == 1


# ── ontology upgrades (Step 11) ───────────────────────────────────────────────

def _graph_meta(driver):
    return _run(driver, f"MATCH (m:`{GRAPH_META}`) "
                        "RETURN m.ontology_version AS version, m.schema_sha256 AS sha256")


@pytest.fixture
def recorded_as(unmarked):
    """Set :GraphMeta to another version; put the code's back afterwards."""
    def record(version, sha256="0" * 64):
        unmarked.execute_query(f"MATCH (m:`{GRAPH_META}`) SET m.ontology_version = $v, "
                               "m.schema_sha256 = $h", v=version, h=sha256, database_=DATABASE)
    yield record
    record(ONTOLOGY_VERSION, SCHEMA_TEXT_SHA256)


def test_a_rebuild_upgrades_an_older_graph_meta_and_reports_it(
    unmarked, rows, inputs, recorded_as,
):
    load_graph(unmarked, rows, inputs, rebuild=False, communities=None)
    recorded_as(ONTOLOGY_VERSION - 1)
    result = load_graph(unmarked, rows, inputs, rebuild=True, communities=None)
    assert result.upgraded_from == ONTOLOGY_VERSION - 1
    assert result.validation.ok
    assert _graph_meta(unmarked) == [{"version": ONTOLOGY_VERSION, "sha256": SCHEMA_TEXT_SHA256}]
    assert load_report("abc123", inputs, result)["graph_meta_upgraded_from"] == ONTOLOGY_VERSION - 1


def test_a_rebuild_at_the_codes_version_upgrades_nothing(unmarked, rows, inputs):
    result = load_graph(unmarked, rows, inputs, rebuild=True, communities=None)
    assert result.upgraded_from is None
    assert load_report("abc123", inputs, result)["graph_meta_upgraded_from"] is None


@pytest.mark.parametrize("version", [ONTOLOGY_VERSION + 1, "3", None, 2.5, True])
def test_a_newer_or_unparseable_version_is_refused_with_nothing_changed(
    unmarked, rows, inputs, recorded_as, version,
):
    load_graph(unmarked, rows, inputs, rebuild=False, communities=None)
    before = _counts(unmarked)
    recorded_as(version)
    with pytest.raises(GraphMetaMismatch):
        load_graph(unmarked, rows, inputs, rebuild=True, communities=None)
    assert _counts(unmarked) == before
    assert _graph_meta(unmarked) == [{"version": version, "sha256": "0" * 64}]


def test_an_older_version_is_not_upgraded_without_rebuild(unmarked, rows, inputs, recorded_as):
    recorded_as(ONTOLOGY_VERSION - 1)
    with pytest.raises(GraphMetaMismatch):
        load_graph(unmarked, rows, inputs, rebuild=False, communities=None)
    assert _graph_meta(unmarked)[0]["version"] == ONTOLOGY_VERSION - 1
    assert not _counts(unmarked)


def test_a_rejected_upgrade_keeps_the_older_graph_meta(unmarked, rows, inputs, recorded_as):
    result = load_graph(unmarked, rows, inputs, rebuild=False, communities=None)
    recorded_as(ONTOLOGY_VERSION - 1)
    bad = Batch(nodes=(*result.built.batch.nodes, NodeRecord("Organization", {"key": "x"})),
                edges=result.built.batch.edges)
    with pytest.raises(BatchRejected):
        rebuild_graph(unmarked, bad, upgrade_from=ONTOLOGY_VERSION - 1)
    assert _graph_meta(unmarked)[0]["version"] == ONTOLOGY_VERSION - 1


def test_the_load_report_records_where_the_evidence_came_from_and_the_bodies(
    unmarked, rows, inputs,
):
    result = load_graph(unmarked, rows, inputs, rebuild=False, communities=None)
    report = load_report("abc123", inputs, result)
    assert report["ontology_version"] == ONTOLOGY_VERSION
    assert report["evidence_provenance"] == {"extract_prompts": [PROMPT],
                                             "extracted_under_ontology": [3]}
    bodies = report["risk_factor_bodies"]
    assert bodies["prompt"] == "body/v1"
    assert bodies["unlocated"] == 2  # rows hold no risk section in this fixture
    assert bodies["body_entries"] == 0 and bodies["long_bodies"] == []


def test_an_agreement_concerning_a_filer_is_written_and_valid_under_version_4(
    unmarked, rows, inputs,
):
    built = load_graph(unmarked, rows, inputs, rebuild=False, communities=None).built.batch
    deal = NodeRef.of("Agreement", key="Agreement:wafer supply agreement")
    quote = evidence(SUPPLY_CHUNK, prompt=PROMPT)
    concerns = EdgeRecord("CONCERNS", deal, NodeRef.of("Company", cik="1045810"), {
        "chunk_ids": [SUPPLY_CHUNK], "evidence_spans": [quote.span],
        "confidences": [quote.confidence], "extract_prompts": [PROMPT]})
    rebuild_graph(unmarked, Batch(
        nodes=(*built.nodes, NodeRecord("Agreement", {"key": deal.key_dict()["key"],
                                                      "name": "wafer supply agreement"},
                                        (quote,))),
        edges=(*built.edges, concerns)))
    assert _run(unmarked, "MATCH (:Agreement)-[:CONCERNS]->(c:Company) RETURN c.cik AS cik") \
        == [{"cik": "1045810"}]
    assert validate_graph(unmarked).ok


# ── communities (Step 12, ontology version 5) ─────────────────────────────────

@pytest.fixture
def communities(rows, inputs):
    """Leiden's communities over the fixture graph, each reportable one with a report
    citing its first member's first evidence chunk."""
    batch = build_graph(rows, inputs.files, inputs.alias_map, inputs.links).batch
    graph = community_graph(batch)
    found = find_communities(graph)
    evidence_of = {n.properties.get("key"): n.evidence[0].chunk_id for n in batch.nodes
                   if n.evidence}
    entries = {c.community_id: ReportEntry(
        c.community_id, c.member_hash, c.level, f"Community {c.community_id}",
        f"It is cited [{evidence_of[c.members[0]]}].", (evidence_of[c.members[0]],),
        TEST_REPORT_PROMPT, f"It is cited [{evidence_of[c.members[0]]}].")
        for c in found if c.reportable}
    return CommunityInputs(
        CommunitiesFile("communities-run1.json", "c" * 64, graph.input_sha256, found),
        ReportsFile("reports-run1.jsonl", "r" * 64, "communities-run1.json", "c" * 64,
                    graph.input_sha256, True, MappingProxyType(entries)))


def test_communities_are_written_in_the_rebuild_and_validate(unmarked, rows, inputs, communities):
    result = load_graph(unmarked, rows, inputs, rebuild=False, communities=communities)

    assert result.validation.ok
    found = communities.communities.communities
    lowers = [c for c in found if c.level == "lower"]
    assert _counts(unmarked)["Community"] == len(found)
    edges = _run(unmarked, "MATCH ()-[e:IN_COMMUNITY|PARENT_OF]->() "
                           "RETURN type(e) AS type, count(*) AS n ORDER BY type")
    assert edges == [{"type": "IN_COMMUNITY", "n": sum(len(c.members) for c in lowers)},
                     {"type": "PARENT_OF", "n": len(lowers)}]
    reported = load_report("abc123", inputs, result)
    assert reported["communities"]["counts"]["lower"] == len(lowers)
    assert reported["communities"]["reports_file"] == "reports-run1.jsonl"
    assert reported["counts"]["nodes"]["Community"] == len(found)


def _without_one_report(communities):
    reportable = next(c for c in communities.communities.communities if c.reportable)
    entries = {k: v for k, v in communities.reports.entries.items()
               if k != reportable.community_id}
    return replace(communities, reports=replace(communities.reports,
                                                entries=MappingProxyType(entries)))


def _stale(communities):
    return replace(communities, communities=replace(communities.communities,
                                                    input_sha256="0" * 64))


@pytest.mark.parametrize("break_inputs", [_without_one_report, _stale],
                         ids=["missing report", "stale communities file"])
def test_communities_that_do_not_fit_are_refused_with_nothing_changed(
    unmarked, rows, inputs, communities, break_inputs,
):
    load_graph(unmarked, rows, inputs, rebuild=False, communities=communities)
    before = _counts(unmarked)

    with pytest.raises(CommunityMismatch):
        load_graph(unmarked, rows, inputs, rebuild=True, communities=break_inputs(communities))

    assert _counts(unmarked) == before


def test_a_version_4_graph_is_upgraded_with_its_communities(
    unmarked, rows, inputs, communities, recorded_as,
):
    load_graph(unmarked, rows, inputs, rebuild=False, communities=None)
    recorded_as(4)

    result = load_graph(unmarked, rows, inputs, rebuild=True, communities=communities)

    assert result.upgraded_from == 4 and result.validation.ok
    assert _graph_meta(unmarked) == [{"version": 5, "sha256": SCHEMA_TEXT_SHA256}]
    assert _counts(unmarked)["Community"] == len(communities.communities.communities)


def test_the_load_command_exits_2_on_communities_that_do_not_fit(monkeypatch, capsys, tmp_path):
    import graph.load

    def refuse(_rebuild):
        raise CommunityMismatch("reportable community lower-x has no report")

    monkeypatch.setattr(graph.load, "_run", refuse)
    assert graph.load.main(["--rebuild"], out_dir=tmp_path) == 2
    assert "nothing was written" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())
