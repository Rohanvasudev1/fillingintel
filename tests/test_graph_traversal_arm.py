"""The graph_traversal arm through ``run`` on ``neo4j-test`` and a throwaway Postgres schema
(Step 11 ticket 06).

The graph is ``tests/graph_local_data.py``'s, around real fixture chunk IDs.  The
Cypher model replays recorded replies (``tests/traversal_data.py``) or scripted
ones written into a recorded body; the guard and executor are the real ones.
Needs ``DATABASE_URL`` and ``NEO4J_TEST_URI``.
"""
import json

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from graph.constraints import apply_constraints
from graph.write import write_batch
from ingest.chunker import chunk_filing
from ingest.store import load_filing
from retrieve.answer_model import AnswerModelError, ApiResponse
from retrieve.arm import ArmError
from retrieve.arm_steps import corpus_filings
from retrieve.arms import ARMS
from retrieve.cypher_prompt import PUBLISHED, load_prompt
from retrieve.graph_traversal import GraphTraversalArm
from retrieve.query_cache import CachedBatchEmbedder
from retrieve.tracing import CYPHER_ATTR, SpanRecorder
from tests.anthropic_fixtures import with_text
from tests.conftest import FIXTURE_NAMES, throwaway_schema
from tests.graph_local_data import (
    K_BLACKWELL_CHUNK,
    K_RISK_CHUNK,
    Q_RISK_CHUNK,
    QUERY_VECTOR,
    TEXTS,
    FakeNodeEmbedder,
    graph_local_batch,
)
from tests.graph_test_data import EXPORT_CHUNK, EXPORT_CONTROLS, SUPPLY_CHUNK, TSMC
from tests.traversal_data import FILINGS, QUESTIONS, recorded
from tests.vector_fakes import MODEL, FakeQueryEmbedder, ScriptedAnswerModel

LOADED = ("nvda_10k", "nvda_10q", "amd_10k")
SIMILARITIES = {EXPORT_CONTROLS: 0.9, TSMC: 0.8}  # graph_local's top chunks on a fallback
LOCAL_TOP = [EXPORT_CHUNK, SUPPLY_CHUNK]
UNKNOWN_LABEL = "MATCH (s:Supplier) RETURN [] AS chunk_ids"
NOTHING = ("MATCH (p:Product) WHERE toLower(p.name) CONTAINS 'no such product' "
           "RETURN collect(p.key) AS chunk_ids")
FAILING = {
    "invalid": "LOAD CSV FROM 'file:///x.csv' AS row RETURN [] AS chunk_ids",
    "error": "UNWIND [0] AS x RETURN [toString(1 / x)] AS chunk_ids",
    "timeout": ("UNWIND range(1, 1000000000) AS x WITH x WHERE x % 7 = 123456 "
                "RETURN collect(toString(x)) AS chunk_ids"),
    "empty": NOTHING,
}
EXPECTED = {  # each recorded question's chunks on the test graph
    "supplier": [SUPPLY_CHUNK],
    "platform": [K_BLACKWELL_CHUNK],
    "persistence": [K_RISK_CHUNK, Q_RISK_CHUNK],
}


class ScriptedCypherModel:
    """Returns the given response bodies in order and keeps every request."""

    def __init__(self, *bodies: dict, error: Exception | None = None, cached: bool = False):
        self._bodies = list(bodies)
        self._error = error
        self._cached = cached
        self.requests = []

    def complete(self, request) -> ApiResponse:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return ApiResponse(self._bodies.pop(0), api_ms=900.0, from_cache=self._cached)


def _reply(cypher: str) -> dict:
    """A recorded body whose reply is *cypher*."""
    return with_text(recorded("platform")["response"],
                     json.dumps({"reasoning": "scripted", "cypher": cypher}))


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
    return neo4j_driver


def _arm(corpus, graph, tmp_path, cypher_model, *, answer_model=None,
         **kwargs) -> GraphTraversalArm:
    reports = tmp_path / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "load-run4.json").write_text("{}", encoding="utf-8")
    nodes = CachedBatchEmbedder(FakeNodeEmbedder(SIMILARITIES), cache_dir=tmp_path / "nodes")
    nodes.embed(list(TEXTS.values()), MODEL, "document")
    return GraphTraversalArm(corpus, graph, FakeQueryEmbedder(QUERY_VECTOR), nodes,
                             cypher_model, answer_model or ScriptedAnswerModel(),
                             load_reports=reports, **kwargs)


def _ids(result) -> list[str]:
    return [r.chunk_id for r in result.retrieved]


# The replies were recorded under ontology 4. Ontology 5 (Step 12) added Community to
# the schema text the prompt embeds, which moved its computed hash; the prompt file and
# the labels the recorded queries use are unchanged.
RECORDED_UNDER = "cypher/v1@5669b1ca"


def test_the_test_setting_matches_the_recorded_prompt(corpus):
    assert set(corpus_filings(corpus)) == set(FILINGS)
    current = load_prompt(filings=FILINGS)
    assert current.file_sha256 == PUBLISHED[current.version]
    assert RECORDED_UNDER.startswith(f"cypher/{current.version}@")
    for name in QUESTIONS:
        assert recorded(name)["prompt_version"] == RECORDED_UNDER


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_a_recorded_query_retrieves_its_evidence(corpus, graph, tmp_path, name):
    model = ScriptedCypherModel(recorded(name)["response"])
    answers = ScriptedAnswerModel()
    result = _arm(corpus, graph, tmp_path, model, answer_model=answers).run(QUESTIONS[name])
    assert _ids(result) == EXPECTED[name]
    (attempt,) = result.graph.cypher_attempts
    assert attempt.outcome == "ok"
    assert attempt.query == json.loads(recorded(name)["response"]["content"][-1]["text"])["cypher"]
    assert result.graph.fallback_reason is None
    assert result.graph.anchors == ()
    assert (result.graph.first_pass_valid, result.graph.non_empty) == (True, True)
    assert list(result.graph.stages) == ["cypher_generation", "cypher_execution"]
    assert result.graph.stages["cypher_generation"].cost_usd > 0
    assert result.graph.stages["cypher_generation"].ms == pytest.approx(900.0)
    assert len(answers.requests) == 1
    assert result.answer.status == "answered"


def test_each_traversal_chunk_carries_its_best_evidence_confidence(corpus, graph, tmp_path):
    # Chunk 13: the regulation's own evidence is implied, SUBJECT_TO's stated.
    query = ("MATCH (r:Regulation) MATCH (r)-[:EVIDENCED_BY]->(c:Chunk) "
             "RETURN collect(c.chunk_id) AS chunk_ids")
    result = _arm(corpus, graph, tmp_path, ScriptedCypherModel(_reply(query))).run(
        QUESTIONS["supplier"])
    assert [(r.chunk_id, r.confidence) for r in result.retrieved] == [(EXPORT_CHUNK, "stated")]


def test_the_question_filter_drops_another_filings_chunks(corpus, graph, tmp_path):
    # The persistence query returns both NVIDIA filings' chunks; a question naming only
    # the 10-K keeps the 10-K's.
    model = ScriptedCypherModel(recorded("persistence")["response"])
    result = _arm(corpus, graph, tmp_path, model).run(
        "How did NVIDIA describe its export-control risk factor in its fiscal 2026 10-K?")
    assert _ids(result) == [K_RISK_CHUNK]


def test_a_guard_failure_is_repaired_with_the_failed_query_and_message(corpus, graph, tmp_path):
    model = ScriptedCypherModel(_reply(UNKNOWN_LABEL), recorded("platform")["response"])
    result = _arm(corpus, graph, tmp_path, model).run(QUESTIONS["platform"])
    assert _ids(result) == [K_BLACKWELL_CHUNK]
    first, second = result.graph.cypher_attempts
    assert (first.outcome, first.rule, first.query) == ("invalid", "ontology", UNKNOWN_LABEL)
    assert "Supplier" in first.message
    assert second.outcome == "ok"
    assert (result.graph.first_pass_valid, result.graph.valid_after_repair) == (False, True)
    assert result.graph.fallback_reason is None
    repair = model.requests[1].params()["messages"]
    assert [m["role"] for m in repair] == ["user", "assistant", "user"]
    assert UNKNOWN_LABEL in repair[2]["content"] and first.message in repair[2]["content"]
    assert list(result.graph.stages) == ["cypher_generation", "cypher_repair",
                                         "cypher_execution"]


def test_an_empty_result_is_repaired_then_falls_back_to_local_search(corpus, graph, tmp_path):
    model = ScriptedCypherModel(_reply(NOTHING), _reply(NOTHING))
    result = _arm(corpus, graph, tmp_path, model).run(QUESTIONS["supplier"])
    assert [a.outcome for a in result.graph.cypher_attempts] == ["empty", "empty"]
    assert result.graph.cypher_attempts[0].rows == 1
    first = result.graph.cypher_attempts[0]
    assert first.message in model.requests[1].params()["messages"][2]["content"]
    assert result.graph.fallback_reason == "empty"
    assert _ids(result)[:2] == LOCAL_TOP
    assert result.graph.anchors[0].key == "regulation:us-export-controls"
    assert result.graph.hops == 1
    assert (result.graph.first_pass_valid, result.graph.valid_after_repair,
            result.graph.non_empty) == (True, True, False)
    assert set(result.graph.stages) == {"cypher_generation", "cypher_repair", "cypher_execution",
                                        "anchor_linking"}


@pytest.mark.parametrize("reason", sorted(FAILING))
def test_every_fallback_reason_is_recorded(corpus, graph, tmp_path, reason):
    query = FAILING[reason]
    model = ScriptedCypherModel(_reply(query), _reply(query))
    result = _arm(corpus, graph, tmp_path, model, cypher_timeout=1.0).run(QUESTIONS["supplier"])
    assert [a.outcome for a in result.graph.cypher_attempts] == [reason, reason]
    assert result.graph.fallback_reason == reason
    assert _ids(result)[:2] == LOCAL_TOP
    assert all(a.message for a in result.graph.cypher_attempts)


def test_a_reply_with_no_query_is_invalid_and_repaired(corpus, graph, tmp_path):
    no_query = with_text(recorded("platform")["response"], "I cannot write that query.")
    model = ScriptedCypherModel(no_query, recorded("platform")["response"])
    result = _arm(corpus, graph, tmp_path, model).run(QUESTIONS["platform"])
    first, second = result.graph.cypher_attempts
    assert (first.outcome, first.rule, first.query) == ("invalid", "reply", "")
    assert second.outcome == "ok"
    assert model.requests[1].params()["messages"][1]["content"] == "I cannot write that query."


def test_a_graph_with_another_schema_hash_stops_the_arm(corpus, graph, tmp_path):
    graph.execute_query("MATCH (m:GraphMeta) SET m.schema_sha256 = 'another schema'")
    with pytest.raises(ArmError, match="ontology"):
        _arm(corpus, graph, tmp_path, ScriptedCypherModel())


def test_config_records_the_cypher_model_and_prompt(corpus, graph, tmp_path):
    arm = _arm(corpus, graph, tmp_path, ScriptedCypherModel())
    prompt = load_prompt(filings=FILINGS)
    assert arm.name == "graph_traversal"
    assert arm.config.graph.cypher_prompt_version == prompt.prompt_version
    assert arm.config.graph.cypher_prompt_sha256 == prompt.sha256
    assert arm.config.graph.load_run == "load-run4"
    assert dict(arm.config.models) == {"embedding": MODEL, "cypher": "claude-sonnet-5-5",
                                       "answer": "claude-sonnet-5-5"}
    assert dict(arm.config.efforts) == {"cypher": "high", "answer": "high"}


def test_a_failed_cypher_call_is_an_arm_error(corpus, graph, tmp_path):
    model = ScriptedCypherModel(error=AnswerModelError("Anthropic API call failed: HTTP 529"))
    with pytest.raises(ArmError, match="graph_traversal arm failed.*529"):
        _arm(corpus, graph, tmp_path, model).run(QUESTIONS["platform"])


def _spans(capture_text: bool):
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, SpanRecorder(provider.get_tracer("test"), capture_text=capture_text)


def test_spans_carry_the_cypher_but_no_prompt_text(corpus, graph, tmp_path):
    exporter, spans = _spans(capture_text=False)
    model = ScriptedCypherModel(recorded("platform")["response"])
    _arm(corpus, graph, tmp_path, model, spans=spans).run(QUESTIONS["platform"])
    finished = exporter.get_finished_spans()
    assert [s.name for s in finished] == ["question_filter", "query_embedding",
                                          "cypher_generation", "cypher_guard", "cypher_execute",
                                          "answer_generation"]
    generation = next(s for s in finished if s.name == "cypher_generation")
    assert generation.attributes[CYPHER_ATTR].startswith("MATCH")
    assert not [k for k in generation.attributes if k.startswith("llm.input_messages")]


def test_text_capture_puts_the_prompt_on_the_generation_span(corpus, graph, tmp_path):
    exporter, spans = _spans(capture_text=True)
    model = ScriptedCypherModel(recorded("platform")["response"])
    _arm(corpus, graph, tmp_path, model, spans=spans).run(QUESTIONS["platform"])
    generation = next(s for s in exporter.get_finished_spans() if s.name == "cypher_generation")
    assert [k for k in generation.attributes if k.startswith("llm.input_messages")]


def test_a_fallback_is_a_span_around_local_search(corpus, graph, tmp_path):
    exporter, spans = _spans(capture_text=False)
    model = ScriptedCypherModel(_reply(NOTHING), _reply(NOTHING))
    _arm(corpus, graph, tmp_path, model, spans=spans).run(QUESTIONS["supplier"])
    names = [s.name for s in exporter.get_finished_spans()]
    assert names[-4:] == ["anchor_linking", "graph_expand", "fallback", "answer_generation"]


def test_the_arm_is_registered_with_the_settings_it_needs():
    spec = ARMS["graph_traversal"]
    assert set(spec.required_env) == {"NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
                                      "DATABASE_URL", "VOYAGE_API_KEY", "ANTHROPIC_API_KEY"}
