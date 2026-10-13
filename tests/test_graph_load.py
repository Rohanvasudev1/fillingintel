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
from graph.build_input import ChunkRow, FilingRow, StructuralRows
from graph.connection import DATABASE
from graph.constraints import GRAPH_META, apply_constraints
from graph.load import GraphNotEmpty, load_graph, read_inputs
from graph.ontology import LABELS_BY_NAME, ONTOLOGY_VERSION, Kind
from graph.write import BatchRejected, rebuild_graph
from tests.graph_test_data import AMD_10K, NVDA_10K
from tests.neo4j_fixtures import MARKER_LABEL

PROMPT = "extract/v2@34a82e2b"


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
        chunks=tuple(ChunkRow(c, c.split(":")[0], section)
                     for c, (section, _) in sorted(test_graph_chunks.items())),
    )


@pytest.fixture
def inputs(tmp_path, graph_test_batch):
    """Extract files for both fixture 10-Ks, an alias map and the extraction reports."""
    extract_dir, reports_dir = tmp_path / "extract", tmp_path / "extraction"
    reports_dir.mkdir()
    for accession_no in (NVDA_10K, AMD_10K):
        nodes = tuple(_v2(n) for n in graph_test_batch.nodes
                      if LABELS_BY_NAME[n.label].kind is Kind.EXTRACTED
                      and n.evidence[0].chunk_id.startswith(accession_no))
        edges = tuple(_v2_edge(e) for e in graph_test_batch.edges if "chunk_ids" in e.properties
                      and e.properties["chunk_ids"][0].startswith(accession_no))
        run = ExtractionRun(accession_no, PROMPT, nodes, edges, (), (), (), (), (), ())
        write_candidates(extract_dir / f"{accession_no}-run2.jsonl", candidate_lines(run))
        (reports_dir / f"{accession_no}-run2.json").write_text(
            '{"failed_chunks": [{"chunk_id": "' + accession_no + ':0001", "reason": "x"}]}')
    alias_map = tmp_path / "alias_map.json"
    alias_map.write_text('{"org:tsmc": "org:tsmc"}')
    return read_inputs(extract_dir, alias_map, reports_dir)


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
