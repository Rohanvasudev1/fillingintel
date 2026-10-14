"""Rebuilding the graph from the fixture filings in the Neo4j test instance (Step 10).

The NVIDIA FY2026 and AMD FY2025 10-K fixtures give the structural rows (every
chunk the chunker makes); extract files written by extraction's own writer give
the extracted records. A rebuild deletes every node but :GraphMeta, so the
`unmarked` fixture removes the test marker first and puts it back after.
Skips without NEO4J_TEST_URI.
"""
from collections import Counter
from dataclasses import replace

import pytest

from extract.pipeline import ExtractionRun
from extract.report import candidate_lines, write_candidates
from graph.batch import Batch, NodeRecord
from graph.build_input import ChunkRow, FilingRow, InputError, StructuralRows
from graph.connection import DATABASE
from graph.constraints import GRAPH_META, GraphMetaMismatch, apply_constraints
from graph.load import load_graph, read_inputs
from graph.load_report import load_report
from graph.ontology import LABELS_BY_NAME, ONTOLOGY_VERSION, Kind
from graph.schema_text import SCHEMA_TEXT_SHA256
from graph.write import BatchRejected, GraphNotEmpty, rebuild_graph
from persist.decisions import LinkEvidence
from persist.links import EXACT_TITLE, PersistLink
from persist.paths import write_links
from tests.graph_test_data import AMD_10K, EXPORT_CHUNK, NVDA_10K, QUOTES, SUPPLY_CHUNK, evidence
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
    result = load_graph(unmarked, rows, inputs, rebuild=False)
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
    load_graph(unmarked, rows, inputs, rebuild=False)
    first = _counts(unmarked)
    with pytest.raises(GraphNotEmpty):
        load_graph(unmarked, rows, inputs, rebuild=False)
    load_graph(unmarked, rows, inputs, rebuild=True)
    assert _counts(unmarked) == first


def test_a_rejected_rebuild_leaves_the_previous_graph(unmarked, rows, inputs):
    result = load_graph(unmarked, rows, inputs, rebuild=False)
    before = _counts(unmarked)
    bad = Batch(nodes=(*result.built.batch.nodes, NodeRecord("Organization", {"key": "x"})),
                edges=result.built.batch.edges)
    with pytest.raises(BatchRejected):
        rebuild_graph(unmarked, bad)
    assert _counts(unmarked) == before


def test_graph_meta_survives_a_rebuild(unmarked, rows, inputs):
    load_graph(unmarked, rows, inputs, rebuild=False)
    load_graph(unmarked, rows, inputs, rebuild=True)
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
    result = load_graph(unmarked, rows, inputs, rebuild=False)
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

    result = load_graph(unmarked, rows, inputs, rebuild=False)
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
    load_graph(unmarked, rows, inputs, rebuild=False)
    recorded_as(ONTOLOGY_VERSION - 1)
    result = load_graph(unmarked, rows, inputs, rebuild=True)
    assert result.upgraded_from == ONTOLOGY_VERSION - 1
    assert result.validation.ok
    assert _graph_meta(unmarked) == [{"version": ONTOLOGY_VERSION, "sha256": SCHEMA_TEXT_SHA256}]
    assert load_report("abc123", inputs, result)["graph_meta_upgraded_from"] == ONTOLOGY_VERSION - 1


def test_a_rebuild_at_the_codes_version_upgrades_nothing(unmarked, rows, inputs):
    result = load_graph(unmarked, rows, inputs, rebuild=True)
    assert result.upgraded_from is None
    assert load_report("abc123", inputs, result)["graph_meta_upgraded_from"] is None


@pytest.mark.parametrize("version", [ONTOLOGY_VERSION + 1, "3", None, 2.5, True])
def test_a_newer_or_unparseable_version_is_refused_with_nothing_changed(
    unmarked, rows, inputs, recorded_as, version,
):
    load_graph(unmarked, rows, inputs, rebuild=False)
    before = _counts(unmarked)
    recorded_as(version)
    with pytest.raises(GraphMetaMismatch):
        load_graph(unmarked, rows, inputs, rebuild=True)
    assert _counts(unmarked) == before
    assert _graph_meta(unmarked) == [{"version": version, "sha256": "0" * 64}]


def test_an_older_version_is_not_upgraded_without_rebuild(unmarked, rows, inputs, recorded_as):
    recorded_as(ONTOLOGY_VERSION - 1)
    with pytest.raises(GraphMetaMismatch):
        load_graph(unmarked, rows, inputs, rebuild=False)
    assert _graph_meta(unmarked)[0]["version"] == ONTOLOGY_VERSION - 1
    assert not _counts(unmarked)


def test_a_rejected_upgrade_keeps_the_older_graph_meta(unmarked, rows, inputs, recorded_as):
    result = load_graph(unmarked, rows, inputs, rebuild=False)
    recorded_as(ONTOLOGY_VERSION - 1)
    bad = Batch(nodes=(*result.built.batch.nodes, NodeRecord("Organization", {"key": "x"})),
                edges=result.built.batch.edges)
    with pytest.raises(BatchRejected):
        rebuild_graph(unmarked, bad, upgrade_from=ONTOLOGY_VERSION - 1)
    assert _graph_meta(unmarked)[0]["version"] == ONTOLOGY_VERSION - 1


def test_the_load_report_records_where_the_evidence_came_from_and_the_bodies(
    unmarked, rows, inputs,
):
    report = load_report("abc123", inputs, load_graph(unmarked, rows, inputs, rebuild=False))
    assert report["ontology_version"] == ONTOLOGY_VERSION
    assert report["evidence_provenance"] == {"extract_prompts": [PROMPT],
                                             "extracted_under_ontology": [3]}
    bodies = report["risk_factor_bodies"]
    assert bodies["prompt"] == "body/v1"
    assert bodies["unlocated"] == 2  # rows hold no risk section in this fixture
    assert bodies["body_entries"] == 0 and bodies["long_bodies"] == []
