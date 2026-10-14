"""``python -m eval.run`` with a stub graph arm (Step 11, ticket 02).

No graph arm exists yet.  The stub returns what a graph arm will: anchors, a hop
count, Cypher attempts, a fallback reason, per-stage costs, each chunk's best
evidence confidence and the graph it read, and it records the graph spans.
Three questions cover the cases: q0072 is answered from a first-pass query;
q0073 links no anchor, so its retrieval is empty and its answer not found;
q0113's query fails the guard, its repair returns nothing, and it falls back
to local search.  Nothing here needs a database.
"""
import json
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from types import MappingProxyType

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from eval.run import EXIT_RUN_ERROR, main
from eval.schema import dump_records, load_records
from graph.ontology import Confidence
from retrieve.answer import write_answer
from retrieve.answer_model import TokenUsage
from retrieve.answer_prompt import SourceChunk, load_prompt
from retrieve.arm import (
    Anchor,
    ArmConfig,
    ArmResult,
    ArmSpec,
    CypherAttempt,
    GraphProvenance,
    GraphRetrieval,
    RetrievedChunk,
    StageCost,
)
from retrieve.pricing import anthropic_cost, embedding_cost
from retrieve.question_filter import parse_question_filter
from retrieve.tracing import (
    ANCHOR_SPAN,
    CYPHER_ATTR,
    CYPHER_EXECUTE_SPAN,
    CYPHER_GENERATION_SPAN,
    CYPHER_GUARD_SPAN,
    EXPAND_SPAN,
    FALLBACK_SPAN,
    TEXT_ENV,
    Kind,
    SpanRecorder,
    anchor_attributes,
    cypher_attributes,
    cypher_reply_attributes,
    fallback_attributes,
    generation_request_attributes,
    retrieval_attributes,
)
from tests.corpus_filings import corpus_filings
from tests.test_eval_run import (
    _ALL,
    ANSWER_MODEL,
    EMBED_MODEL,
    FakeArm,
    FakeJudge,
    ReplayModel,
    _judge_spec,
)

IDS = ["q0072", "q0073", "q0113"]  # lookup, lookup, multi_hop
TODAY = date(2026, 10, 14)
CYPHER_MODEL = "claude-sonnet-5-5"
FILINGS = corpus_filings()
PROVENANCE = GraphProvenance(ontology_version=4, schema_sha256="ab" * 32, load_run="load-run4",
                             cypher_prompt_version="cypher/v1@1a2b3c4d",
                             cypher_prompt_sha256="cd" * 32)
GOOD = "MATCH (r:RiskFactor)-[:EVIDENCED_BY]->(c:Chunk) RETURN collect(c.chunk_id) AS chunk_ids"
BAD = "MATCH (r:Riskfactor) RETURN r"
EMPTY = "MATCH (r:RiskFactor {key: 'none'}) RETURN [] AS chunk_ids"
SYSTEM, USER = "cypher system prompt text", "cypher user prompt text"
USAGE = TokenUsage(input_tokens=3000, output_tokens=400, cache_read_input_tokens=1000)
CYPHER_COST = anthropic_cost(CYPHER_MODEL, USAGE)
EMBED_COST = embedding_cost(EMBED_MODEL, 10)
LINK = StageCost(ms=20.0)
GENERATE = StageCost(ms=900.0, cost_usd=CYPHER_COST, input_tokens=4000, output_tokens=400)
REPAIR = StageCost(ms=700.0, cost_usd=CYPHER_COST, input_tokens=4000, output_tokens=400,
                   from_cache=True)
EXECUTE = StageCost(ms=30.0)
ANCHOR = Anchor(key="RiskFactor:0001045810-26-000021:export controls", label="RiskFactor",
                similarity=0.81, matched_by="embedding")
NAMED = Anchor(key="Organization:tsmc", label="Organization", similarity=1.0, matched_by="name")


def _graph(record_id: str) -> GraphRetrieval:
    if record_id == "q0072":
        return GraphRetrieval(
            stages=MappingProxyType({"cypher_generation": GENERATE, "cypher_execution": EXECUTE}),
            cypher_attempts=(CypherAttempt(GOOD, "ok", rows=1),),
        )
    if record_id == "q0073":
        return GraphRetrieval(stages=MappingProxyType({"anchor_linking": LINK}), hops=1,
                              no_anchor=True)
    return GraphRetrieval(
        stages=MappingProxyType({"cypher_generation": GENERATE, "cypher_repair": REPAIR,
                                 "cypher_execution": EXECUTE, "anchor_linking": LINK}),
        anchors=(NAMED, ANCHOR),
        hops=1,
        cypher_attempts=(
            CypherAttempt(BAD, "invalid", rule="ontology", message="unknown label Riskfactor"),
            CypherAttempt(EMPTY, "empty"),
        ),
        fallback_reason="empty",
    )


def _ranking(record_id: str) -> list[str]:
    if record_id == "q0073":
        return []
    return [_ALL[record_id].gold_chunk_ids[0], "0001045810-26-000075:0000"]


def _reply(record_id: str, ranking: list[str]) -> str:
    if not ranking:
        return "STATUS: not_found"
    return f"STATUS: answered\nA rose [{ranking[0]}]."


class StubGraphArm:
    name = "graph_stub"

    def __init__(self, spans: SpanRecorder, models: dict[str, str] | None = None):
        self.config = ArmConfig(
            models=MappingProxyType(models or {"embedding": EMBED_MODEL, "answer": ANSWER_MODEL,
                                               "cypher": CYPHER_MODEL}),
            k=10,
            chunker_version="1",
            efforts=MappingProxyType({"answer": "high", "cypher": "high"}),
            answer_prompt=load_prompt("v1"),
            answer_max_tokens=16_000,
            graph=PROVENANCE,
        )
        self._spans = spans
        self._ids = {_ALL[i].question: i for i in IDS}
        self.questions: list[str] = []

    def run(self, question: str) -> ArmResult:
        self.questions.append(question)
        record_id = self._ids[question]
        graph = _graph(record_id)
        self._record_spans(graph)
        ranking = _ranking(record_id)
        retrieved = tuple(RetrievedChunk(cid, 2.0 - i, Confidence.STATED if i == 0
                                         else Confidence.UNCERTAIN)
                          for i, cid in enumerate(ranking))
        chunks = [SourceChunk(cid, "NVDA", "10-K", "FY2026", "item_1a", "text") for cid in ranking]
        model = ReplayModel(_reply(record_id, ranking), 800.0, from_cache=False)
        return ArmResult(
            retrieved=retrieved,
            question_filter=parse_question_filter(question, FILINGS),
            companies_without_chunks=(),
            embed_ms=2.0,
            search_ms=sum(s.ms for s in graph.stages.values()),
            query_cached=True,
            embed_tokens=10,
            embed_cost_usd=EMBED_COST,
            answer=write_answer(question, chunks, model, load_prompt("v1")),
            sources=tuple(chunks),
            graph=graph,
        )

    def _record_spans(self, graph: GraphRetrieval) -> None:
        for attempt in graph.cypher_attempts:
            request = generation_request_attributes(CYPHER_MODEL, "high", 8000,
                                                    PROVENANCE.cypher_prompt_version)
            with self._spans.span(CYPHER_GENERATION_SPAN, Kind.LLM, request) as span:
                messages = ((SYSTEM, USER, attempt.query) if self._spans.capture_text else None)
                span.set_attributes(cypher_reply_attributes(
                    attempt.query, cost_usd=CYPHER_COST, from_cache=False, usage=USAGE,
                    messages=messages))
            with self._spans.span(CYPHER_GUARD_SPAN, Kind.GUARDRAIL,
                                  cypher_attributes(attempt)):
                pass
            if attempt.valid:
                with self._spans.span(CYPHER_EXECUTE_SPAN, Kind.RETRIEVER,
                                      cypher_attributes(attempt)):
                    pass
        if graph.fallback_reason is not None:
            with self._spans.span(FALLBACK_SPAN, Kind.CHAIN,
                                  fallback_attributes(graph.fallback_reason)):
                pass
        if graph.hops is not None:
            with self._spans.span(ANCHOR_SPAN, Kind.RETRIEVER, anchor_attributes(graph.anchors)):
                pass
            with self._spans.span(EXPAND_SPAN, Kind.RETRIEVER, retrieval_attributes(())):
                pass


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("FAKE_ARM_KEY", "not-real")
    monkeypatch.setenv("FAKE_JUDGE_KEY", "not-real")


@pytest.fixture
def eval_dir(tmp_path):
    directory = tmp_path / "eval"
    dump_records([_ALL[i] for i in IDS], directory / "agent_drafted_set.jsonl")
    (directory / "eval_set.jsonl").write_text("", encoding="utf-8")
    return directory


def _memory_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _run(eval_dir: Path, tmp_path: Path, models: dict[str, str] | None = None,
         provider: TracerProvider | None = None) -> tuple[int, list[StubGraphArm], Path]:
    records = load_records(eval_dir / "agent_drafted_set.jsonl")
    opened: list[StubGraphArm] = []

    def open_arm(response_cache: Path, spans=None):
        arm = StubGraphArm(spans or SpanRecorder.off(), models)
        opened.append(arm)
        return nullcontext(arm)

    spec = ArmSpec(name="graph_stub", required_env=(), open=open_arm)
    runs = tmp_path / "runs"
    code = main(["--arm", "graph_stub"], arms={"graph_stub": spec},
                judges=_judge_spec(FakeJudge(records)), eval_dir=eval_dir, runs_dir=runs,
                today=TODAY, response_cache=tmp_path / "responses",
                uncached_root=tmp_path / "uncached", tracing=lambda environ: provider)
    return code, opened, runs


def _document(eval_dir: Path, tmp_path: Path) -> dict:
    code, _, runs = _run(eval_dir, tmp_path)
    assert code == 0
    (path,) = runs.glob("*.json")
    return json.loads(path.read_text())


def _questions(document: dict) -> dict[str, dict]:
    return {q["id"]: q for q in document["questions"]}


def test_the_header_records_the_graph_and_cypher_prompt_the_arm_read(eval_dir, tmp_path):
    header = _document(eval_dir, tmp_path)["header"]
    assert header["graph"] == {
        "ontology_version": 4, "schema_sha256": "ab" * 32, "load_run": "load-run4",
        "cypher_prompt": {"version": "cypher/v1@1a2b3c4d", "sha256": "cd" * 32},
    }
    assert header["models"]["cypher"] == CYPHER_MODEL
    assert header["efforts"]["cypher"] == "high"
    assert CYPHER_MODEL in header["prices"]["models"]


def test_a_question_record_holds_the_anchors_hops_and_no_anchor_flag(eval_dir, tmp_path):
    records = _questions(_document(eval_dir, tmp_path))
    graph = records["q0113"]["graph"]
    assert graph["anchors"] == [
        {"key": "Organization:tsmc", "label": "Organization", "similarity": 1.0,
         "matched_by": "name"},
        {"key": ANCHOR.key, "label": "RiskFactor", "similarity": 0.81, "matched_by": "embedding"},
    ]
    assert graph["hops"] == 1
    assert graph["no_anchor"] is False
    assert records["q0073"]["graph"]["no_anchor"] is True
    assert records["q0073"]["graph"]["anchors"] == []
    assert records["q0072"]["graph"]["hops"] is None


def test_a_question_record_holds_every_cypher_attempt_the_fallback_and_validity(
        eval_dir, tmp_path):
    records = _questions(_document(eval_dir, tmp_path))
    graph = records["q0113"]["graph"]
    assert graph["cypher_attempts"] == [
        {"query": BAD, "outcome": "invalid", "rows": 0, "rule": "ontology",
         "message": "unknown label Riskfactor"},
        {"query": EMPTY, "outcome": "empty", "rows": 0, "rule": None, "message": None},
    ]
    assert graph["fallback_reason"] == "empty"
    # the repair passed the guard and ran, but returned nothing
    assert graph["validity"] == {"first_pass_valid": False, "valid_after_repair": True,
                                 "non_empty": False, "fallback": True}
    assert records["q0072"]["graph"]["validity"] == {
        "first_pass_valid": True, "valid_after_repair": True, "non_empty": True,
        "fallback": False}
    # no query was generated: nothing to rate
    assert records["q0073"]["graph"]["validity"] == {
        "first_pass_valid": None, "valid_after_repair": None, "non_empty": None,
        "fallback": False}


def test_each_retrieved_chunk_carries_its_best_evidence_confidence(eval_dir, tmp_path):
    record = _questions(_document(eval_dir, tmp_path))["q0072"]
    assert [r["confidence"] for r in record["retrieved"]] == ["stated", "uncertain"]


def test_a_question_record_holds_latency_tokens_and_cost_per_graph_stage(eval_dir, tmp_path):
    record = _questions(_document(eval_dir, tmp_path))["q0113"]
    stages = record["graph"]["stages"]
    assert list(stages) == ["anchor_linking", "cypher_generation", "cypher_repair",
                            "cypher_execution"]
    assert stages["cypher_repair"] == {"ms": 700.0, "input_tokens": 4000, "output_tokens": 400,
                                       "cost_usd": pytest.approx(CYPHER_COST),
                                       "from_cache": True}
    cost = record["cost_usd"]
    generation = record["cost_usd"]["generation"]
    assert cost == pytest.approx({
        "embedding": EMBED_COST, "anchor_linking": 0.0, "cypher_generation": CYPHER_COST,
        "cypher_repair": CYPHER_COST, "cypher_execution": 0.0, "generation": generation,
        "total": EMBED_COST + 2 * CYPHER_COST + generation,
    })
    assert record["search_ms"] == 1650.0


def test_cells_report_graph_stage_latency_and_cost(eval_dir, tmp_path):
    cell = _document(eval_dir, tmp_path)["cells"]["agent_drafted"]["graph_stub"]["lookup"]
    latency = cell["latency_ms"]
    # only q0073 linked anchors, and only q0072 generated a query
    assert latency["anchor_linking"]["n"] == 1
    assert latency["anchor_linking"]["p50"] == 20.0
    assert latency["cypher_generation"]["n"] == 1
    assert latency["cypher_repair"]["n"] == 0
    cost = cell["cost_usd"]
    assert cost["per_query_cypher_generation"] == pytest.approx(CYPHER_COST / 2)
    assert cost["per_query_cypher_repair"] == 0.0
    assert "per_query_cypher_generation" in cost["intervals"]


def test_cells_count_no_anchor_empty_retrieval_and_not_found_per_class(eval_dir, tmp_path):
    cells = _document(eval_dir, tmp_path)["cells"]["agent_drafted"]["graph_stub"]
    assert cells["lookup"]["graph"] == {"n": 2, "no_anchor": 1, "empty_retrieval": 1,
                                        "not_found": 1}
    assert cells["multi_hop"]["graph"] == {"n": 1, "no_anchor": 0, "empty_retrieval": 0,
                                           "not_found": 0}


def test_an_arm_model_with_no_price_stops_the_run_before_any_question(
        eval_dir, tmp_path, capsys):
    models = {"embedding": EMBED_MODEL, "answer": ANSWER_MODEL, "cypher": "claude-unpriced-0"}
    code, (arm,), runs = _run(eval_dir, tmp_path, models)
    assert code == EXIT_RUN_ERROR
    assert "claude-unpriced-0" in capsys.readouterr().err
    assert arm.questions == []
    assert not runs.exists()


def _spans(eval_dir, tmp_path) -> list:
    provider, exporter = _memory_provider()
    code, _, _ = _run(eval_dir, tmp_path, provider=provider)
    assert code == 0
    return list(exporter.get_finished_spans())


def test_graph_spans_carry_the_cypher_but_no_prompt_text_by_default(eval_dir, tmp_path):
    spans = _spans(eval_dir, tmp_path)
    names = {s.name for s in spans}
    assert {"anchor_linking", "graph_expand", "cypher_generation", "cypher_guard",
            "cypher_execute", "fallback"} <= names
    generated = [s for s in spans if s.name == "cypher_generation"]
    assert {s.attributes[CYPHER_ATTR] for s in generated} == {GOOD, BAD, EMPTY}
    guards = [s for s in spans if s.name == "cypher_guard"]
    assert all(s.attributes[CYPHER_ATTR] for s in guards)
    fallback = next(s for s in spans if s.name == "fallback")
    assert fallback.attributes["filingintel.fallback.reason"] == "empty"
    text = json.dumps([dict(s.attributes) for s in spans])
    assert SYSTEM not in text and USER not in text


def test_graph_spans_carry_the_cypher_prompt_text_when_switched_on(
        eval_dir, tmp_path, monkeypatch):
    monkeypatch.setenv(TEXT_ENV, "true")
    spans = _spans(eval_dir, tmp_path)
    generated = next(s for s in spans if s.name == "cypher_generation")
    attributes = generated.attributes
    assert attributes["llm.input_messages.0.message.content"] == SYSTEM
    assert attributes["llm.input_messages.1.message.content"] == USER
    assert attributes["llm.output_messages.0.message.content"] == attributes[CYPHER_ATTR]


def test_a_vector_run_file_gains_no_graph_field(tmp_path):
    """The vector-shaped fake arm of tests/test_eval_run.py leaves every graph field out."""
    directory = tmp_path / "eval"
    dump_records([_ALL[i] for i in IDS], directory / "agent_drafted_set.jsonl")
    (directory / "eval_set.jsonl").write_text("", encoding="utf-8")
    records = load_records(directory / "agent_drafted_set.jsonl")
    spec = ArmSpec(name="fake", required_env=("FAKE_ARM_KEY",),
                   open=lambda _cache, _spans=None: nullcontext(FakeArm(records)))
    runs = tmp_path / "runs"
    assert main(["--arm", "fake"], arms={"fake": spec}, judges=_judge_spec(FakeJudge(records)),
                eval_dir=directory, runs_dir=runs, today=TODAY) == 0
    (path,) = runs.glob("*.json")
    document = json.loads(path.read_text())
    assert "graph" not in document["header"]
    for record in document["questions"]:
        assert "graph" not in record
        assert set(record["cost_usd"]) == {"embedding", "generation", "total"}
        assert all(set(r) == {"chunk_id", "score"} for r in record["retrieved"])
    for cell in document["cells"]["agent_drafted"]["fake"].values():
        assert "graph" not in cell
        assert set(cell["latency_ms"]) == {"retrieval", "embed", "search", "generation"}
        assert set(cell["cost_usd"]) == {"per_query_embedding", "per_query_generation",
                                         "per_query_total", "total", "intervals"}
