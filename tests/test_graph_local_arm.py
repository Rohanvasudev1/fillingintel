"""The graph_local arm through ``run`` on ``neo4j-test`` and a throwaway Postgres schema
(Step 11 ticket 05).

The graph is ``tests/graph_local_data.py``'s, around real fixture chunk IDs.
Each test states its node similarities, so the expected ranking follows from the
scoring rule: a chunk scores the sum over anchors of similarity x (1 + the
anchor's edges citing it).  Needs ``DATABASE_URL`` and ``NEO4J_TEST_URI``.
"""
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from graph.constraints import apply_constraints
from graph.ontology import ONTOLOGY_VERSION
from graph.schema_text import SCHEMA_TEXT_SHA256
from graph.write import write_batch
from ingest.chunker import chunk_filing
from ingest.store import load_filing
from retrieve.arm import ArmError, GraphProvenance
from retrieve.arms import ARMS
from retrieve.graph_local import GraphLocalArm
from retrieve.query_cache import CachedBatchEmbedder
from retrieve.tracing import SpanRecorder
from tests.conftest import FIXTURE_NAMES, throwaway_schema
from tests.graph_local_data import (
    BLACKWELL,
    EXTRACTED,
    K_BLACKWELL_CHUNK,
    K_RISK,
    K_RISK_CHUNK,
    NVDA_10Q,
    Q_BLACKWELL_CHUNK,
    Q_RISK,
    Q_RISK_CHUNK,
    QUERY_VECTOR,
    TEXTS,
    FakeNodeEmbedder,
    graph_local_batch,
    key_of,
)
from tests.graph_test_data import (
    COMPUTE,
    CUSTOMERS_CHUNK,
    DATA_CENTER,
    EXPORT_CHUNK,
    EXPORT_CONTROLS,
    HUANG,
    OFFICERS_CHUNK,
    SEGMENTS_CHUNK,
    SUPPLY_CHUNK,
    TOP_CUSTOMER_SHARE,
    TSMC,
)
from tests.neo4j_fixtures import record_test_load_run
from tests.vector_fakes import MODEL, FakeQueryEmbedder, ScriptedAnswerModel

LOADED = ("nvda_10k", "nvda_10q", "amd_10k")
UNFILTERED = "What are the main supply chain risks?"  # names nothing and matches no name
SIMILARITIES = {
    EXPORT_CONTROLS: 0.9, TSMC: 0.8, HUANG: 0.5, COMPUTE: 0.4, DATA_CENTER: 0.35,
    TOP_CUSTOMER_SHARE: 0.3, BLACKWELL: 0.2, K_RISK: 0.1, Q_RISK: 0.05,
}
NOT_FOUND = "STATUS: not_found\n"


@pytest.fixture(scope="module")
def corpus(fixture_records):
    """Postgres holding NVIDIA's 10-K and 10-Q and AMD's 10-K, in a throwaway schema."""
    with throwaway_schema() as conn:
        for name in LOADED:
            record = fixture_records[FIXTURE_NAMES.index(name)]
            load_filing(conn, record, chunk_filing(record.filing))
        yield conn


@pytest.fixture(scope="module")
def chunks(nvda_10k_filing, nvda_10q_filing, amd_10k_filing):
    """(chunk texts, sections) for every chunk of the loaded filings."""
    texts, sections = {}, {}
    for filing in (nvda_10k_filing, nvda_10q_filing, amd_10k_filing):
        for c in chunk_filing(filing):
            texts[c.chunk_id] = filing.text[c.char_start:c.char_end]
            sections[c.chunk_id] = c.section
    return texts, sections


@pytest.fixture
def graph(neo4j_driver, chunks):
    apply_constraints(neo4j_driver)
    write_batch(neo4j_driver, graph_local_batch(*chunks))
    record_test_load_run(neo4j_driver, "load-run4")
    return neo4j_driver


def _node_cache(tmp_path, similarities, texts=tuple(TEXTS.values())) -> CachedBatchEmbedder:
    cache = CachedBatchEmbedder(FakeNodeEmbedder(similarities), cache_dir=tmp_path / "nodes")
    cache.embed(list(texts), MODEL, "document")
    return cache


def _arm(corpus, graph, tmp_path, similarities=SIMILARITIES, *, answer_model=None,
         node_cache=None, **kwargs) -> GraphLocalArm:
    return GraphLocalArm(
        corpus, graph, FakeQueryEmbedder(QUERY_VECTOR),
        node_cache or _node_cache(tmp_path, similarities),
        answer_model or ScriptedAnswerModel(), **kwargs,
    )


def _ids(result) -> list[str]:
    return [r.chunk_id for r in result.retrieved]


def test_chunks_rank_by_summed_anchor_similarity_times_edge_support(corpus, graph, tmp_path):
    result = _arm(corpus, graph, tmp_path).run(UNFILTERED)
    # Chunk 6 (Compute 0.4 x 2 + Data Center 0.35 x 2) ties chunk 16 (Huang 0.5 x 3)
    # at 1.5; the smaller chunk ID comes first.
    assert _ids(result) == [EXPORT_CHUNK, SUPPLY_CHUNK, SEGMENTS_CHUNK, OFFICERS_CHUNK,
                            K_BLACKWELL_CHUNK, CUSTOMERS_CHUNK, K_RISK_CHUNK, Q_RISK_CHUNK,
                            Q_BLACKWELL_CHUNK]
    assert [r.score for r in result.retrieved] == pytest.approx(
        [1.8, 1.6, 1.5, 1.5, 1.1, 0.9, 0.4, 0.35, 0.2])
    assert [(a.key, a.matched_by) for a in result.graph.anchors] == [
        (key_of(ref), "embedding") for ref in sorted(SIMILARITIES, key=SIMILARITIES.get,
                                                       reverse=True)]
    assert result.graph.anchors[0].similarity == pytest.approx(0.9)


def test_the_list_is_cut_to_k(corpus, graph, tmp_path):
    result = _arm(corpus, graph, tmp_path, k=3).run(UNFILTERED)
    assert _ids(result) == [EXPORT_CHUNK, SUPPLY_CHUNK, SEGMENTS_CHUNK]


def test_two_hops_add_the_neighbours_evidence(corpus, graph, tmp_path):
    result = _arm(corpus, graph, tmp_path, hops=2).run(UNFILTERED)
    # Blackwell (0.2) reaches Data Center's chunk 6; Data Center (0.35) reaches
    # Blackwell's 10-Q chunk.
    assert _ids(result) == [EXPORT_CHUNK, SEGMENTS_CHUNK, SUPPLY_CHUNK, OFFICERS_CHUNK,
                            K_BLACKWELL_CHUNK, CUSTOMERS_CHUNK, Q_BLACKWELL_CHUNK, K_RISK_CHUNK,
                            Q_RISK_CHUNK]
    assert [r.score for r in result.retrieved][:2] == pytest.approx([1.8, 1.7])
    assert result.graph.hops == 2


def test_a_persistence_link_reaches_the_other_years_risk_factor(corpus, graph, tmp_path):
    result = _arm(corpus, graph, tmp_path, {K_RISK: 0.9}).run(UNFILTERED)
    # The 10-K factor's own chunk, cited by DISCLOSES and the link: 0.9 x 3; the
    # 10-Q chunk, cited only by the link: 0.9 x 2, plus the 10-Q factor's 0.01 x 3.
    assert _ids(result)[:2] == [K_RISK_CHUNK, Q_RISK_CHUNK]
    assert [r.score for r in result.retrieved][:2] == pytest.approx([2.7 + 0.02, 1.8 + 0.03])


def test_a_name_match_beats_a_closer_embedding(corpus, graph, tmp_path):
    similarities = {**SIMILARITIES, TSMC: 0.01}
    question = "How does NVIDIA depend on Taiwan Semiconductor Manufacturing Company?"
    result = _arm(corpus, graph, tmp_path, similarities).run(question)
    first = result.graph.anchors[0]
    assert (first.key, first.matched_by, first.similarity) == (key_of(TSMC), "name", 1.0)
    assert result.graph.anchors[1].key == key_of(EXPORT_CONTROLS)
    assert _ids(result)[:2] == [SUPPLY_CHUNK, EXPORT_CHUNK]
    assert result.retrieved[0].score == pytest.approx(2.0)


def test_an_alias_matches_by_name(corpus, graph, tmp_path):
    result = _arm(corpus, graph, tmp_path, {**SIMILARITIES, TSMC: 0.01}).run(
        "Which foundry work does TSMC do for NVIDIA?")
    assert result.graph.anchors[0].key == key_of(TSMC)
    assert result.graph.anchors[0].matched_by == "name"


def test_filers_are_never_anchors(corpus, graph, tmp_path):
    result = _arm(corpus, graph, tmp_path).run("How does NVIDIA compete with AMD?")
    assert sorted(a.key for a in result.graph.anchors) == sorted(key_of(r) for r in EXTRACTED)
    assert "Company" not in {a.label for a in result.graph.anchors}


def test_the_question_filter_drops_another_periods_anchors_and_chunks(corpus, graph, tmp_path):
    arm = _arm(corpus, graph, tmp_path)
    filtered = arm.run("What did NVIDIA say about Blackwell in fiscal 2026?")
    assert filtered.question_filter.periods == ("FY2026",)
    assert filtered.graph.anchors[0].key == key_of(BLACKWELL)
    assert key_of(Q_RISK) not in {a.key for a in filtered.graph.anchors}  # 10-Q evidence only
    assert not [c for c in _ids(filtered) if c.startswith(NVDA_10Q)]
    unfiltered = arm.run("What did NVIDIA say about Blackwell?")
    assert Q_BLACKWELL_CHUNK in _ids(unfiltered)


def test_no_anchor_gives_an_empty_retrieval_and_a_not_found_answer(corpus, graph, tmp_path):
    answer_model = ScriptedAnswerModel(lambda ids: NOT_FOUND)
    result = _arm(corpus, graph, tmp_path, answer_model=answer_model).run(
        "What did AMD's fiscal 2025 10-K say about competition?")  # no node has AMD evidence
    assert result.retrieved == ()
    assert result.graph.anchors == ()
    assert result.graph.no_anchor is True
    assert result.answer.status == "not_found"


def test_each_chunk_carries_its_best_evidence_confidence(corpus, graph, tmp_path):
    result = _arm(corpus, graph, tmp_path).run(UNFILTERED)
    # The regulation's own evidence is implied; SUBJECT_TO cites the same chunk as stated.
    assert result.retrieved[0].chunk_id == EXPORT_CHUNK
    assert result.retrieved[0].confidence == "stated"


def test_the_retrieval_records_hops_and_the_anchor_linking_stage(corpus, graph, tmp_path):
    ticks = iter([1.0, 1.1, 1.2, 1.25, 1.5])
    result = _arm(corpus, graph, tmp_path, clock=lambda: next(ticks)).run(UNFILTERED)
    assert result.graph.hops == 1
    assert result.graph.no_anchor is False
    assert list(result.graph.stages) == ["anchor_linking"]
    assert result.graph.stages["anchor_linking"].ms == pytest.approx(50.0)
    assert result.embed_ms == pytest.approx(100.0)
    assert result.search_ms == pytest.approx(400.0)


def test_config_records_the_graph_the_arm_read(corpus, graph, tmp_path):
    record_test_load_run(graph, "load-run12")
    arm = _arm(corpus, graph, tmp_path)
    assert arm.name == "graph_local"
    assert arm.config.graph == GraphProvenance(
        ontology_version=ONTOLOGY_VERSION, schema_sha256=SCHEMA_TEXT_SHA256, load_run="load-run12")
    assert dict(arm.config.models) == {"embedding": MODEL, "answer": "claude-sonnet-5-5"}
    assert arm.config.k == 10


def test_a_graph_from_another_ontology_stops_the_arm(corpus, graph, tmp_path):
    graph.execute_query("MATCH (m:GraphMeta) SET m.ontology_version = 3")
    with pytest.raises(ArmError, match="ontology"):
        _arm(corpus, graph, tmp_path)


def test_a_node_without_a_cached_embedding_stops_the_arm(corpus, graph, tmp_path):
    texts = [t for ref, t in TEXTS.items() if ref != HUANG]
    with pytest.raises(ArmError, match="1 graph node texts have no cached.*retrieve.node_embed"):
        _arm(corpus, graph, tmp_path, node_cache=_node_cache(tmp_path, {}, texts))


def test_a_graph_that_records_no_load_run_stops_the_arm(corpus, graph, tmp_path):
    graph.execute_query("MATCH (m:GraphMeta) REMOVE m.load_run")
    with pytest.raises(ArmError, match="records no load run"):
        _arm(corpus, graph, tmp_path)


def test_the_arm_records_its_retrieval_steps_as_spans(corpus, graph, tmp_path):
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    spans = SpanRecorder(provider.get_tracer("test"))
    _arm(corpus, graph, tmp_path, spans=spans).run(UNFILTERED)
    names = [s.name for s in exporter.get_finished_spans()]
    assert names == ["question_filter", "query_embedding", "anchor_linking", "graph_expand",
                     "answer_generation"]


def test_the_arm_is_registered_with_the_settings_it_needs():
    spec = ARMS["graph_local"]
    assert set(spec.required_env) == {"NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
                                      "DATABASE_URL", "VOYAGE_API_KEY", "ANTHROPIC_API_KEY"}


def test_a_retrieval_failure_is_an_arm_error(corpus, graph, tmp_path):
    arm = GraphLocalArm(corpus, graph, FakeQueryEmbedder((0.0,) * len(QUERY_VECTOR)),
                        _node_cache(tmp_path, SIMILARITIES), ScriptedAnswerModel())
    with pytest.raises(ArmError, match="graph_local arm failed.*zero vector"):
        arm.retrieve(UNFILTERED)
