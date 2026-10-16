"""The graph_global arm through ``run`` on ``neo4j-test`` and a throwaway Postgres schema
(Step 12 ticket 06, ADR-0008).

The graph, reports and fake vectors are ``tests/global_data.py``'s.  The map and
reduce model replays recorded replies by request hash (two real dev global
questions), or scripted replies written into a recorded body.  Selection, the
point check, the reduce input and the answer filter are the real ones.
Needs ``DATABASE_URL`` and ``NEO4J_TEST_URI``.
"""
import json
from collections.abc import Callable

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from eval.graph_report import graph_cell, header_graph, question_graph
from graph.constraints import apply_constraints
from graph.write import write_batch
from ingest.chunker import chunk_filing
from ingest.store import load_filing, save_embedding
from retrieve.answer import DECLINE_TEXT, NOT_FOUND_TEXT
from retrieve.answer_model import AnswerModelError, AnswerRequest, ApiResponse
from retrieve.arm import ArmError
from retrieve.arms import ARMS
from retrieve.global_map import MapRequest
from retrieve.global_prompts import load_map_prompt, load_reduce_prompt
from retrieve.graph_global import GraphGlobalArm
from retrieve.tracing import COMMUNITY_ATTR, SpanRecorder
from tests.anthropic_fixtures import load, with_text
from tests.conftest import FIXTURE_NAMES, throwaway_schema
from tests.global_data import (
    AMD_FY2025,
    CHUNK_SIMILARITIES,
    LOADED,
    QUESTIONS,
    SHOWN_10K,
    SIMILARITIES,
    community_batch,
    load_reports_dir,
    recorded,
    report_cache,
    reports_file,
)
from tests.graph_local_data import (
    K_BLACKWELL_CHUNK,
    K_RISK_CHUNK,
    Q_BLACKWELL_CHUNK,
    QUERY_VECTOR,
    at_similarity,
    graph_local_batch,
)
from tests.graph_test_data import EXPORT_CHUNK, OFFICERS_CHUNK, SEGMENTS_CHUNK, SUPPLY_CHUNK
from tests.vector_fakes import EXCERPT_ID, MODEL, FakeQueryEmbedder

QUESTION = QUESTIONS["foundries"]
COMMUNITY_OF = {shown[0]: c for c, shown in SHOWN_10K.items()}  # first shown chunk -> community
RANKED = [K_BLACKWELL_CHUNK, K_RISK_CHUNK, EXPORT_CHUNK, SUPPLY_CHUNK, SEGMENTS_CHUNK]


def _body(text: str) -> dict:
    return with_text(load("answered_q0072")["response"], text)


def _points(*points: tuple[str, int]) -> dict:
    return _body(json.dumps({"points": [{"text": t, "score": s} for t, s in points]}))


class ScriptedGlobalModel:
    """Answers each map call with the body *maps* gives its community and the reduce call
    with *reduce*; keeps every request."""

    def __init__(self, maps: Callable[[str], dict] | dict, reduce: dict | None = None,
                 error: Exception | None = None):
        self._maps = maps if callable(maps) else maps.__getitem__
        self._reduce = reduce
        self._error = error
        self.requests: list = []

    def complete(self, request) -> ApiResponse:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if isinstance(request, MapRequest):
            community = COMMUNITY_OF[EXCERPT_ID.findall(request.user)[0]]
            return ApiResponse(self._maps(community), api_ms=1000.0)
        assert isinstance(request, AnswerRequest) and self._reduce is not None
        return ApiResponse(self._reduce, api_ms=3000.0)

    @property
    def map_requests(self) -> list[MapRequest]:
        return [r for r in self.requests if isinstance(r, MapRequest)]

    @property
    def reduce_requests(self) -> list[AnswerRequest]:
        return [r for r in self.requests if isinstance(r, AnswerRequest)]


class RecordedGlobalModel:
    """Replays a recorded question's replies by request hash; an unrecorded request fails."""

    def __init__(self, name: str):
        fixture = recorded(name)
        calls = [*fixture["map_calls"], fixture["reduce"]]
        self._bodies = {c["request_sha256"]: (c["response"], c["api_ms"]) for c in calls}
        self.requests: list = []

    def complete(self, request) -> ApiResponse:
        self.requests.append(request)
        body, api_ms = self._bodies[request.cache_key()]
        return ApiResponse(body, api_ms=api_ms, from_cache=True)


def _supply_points(community: str) -> dict:
    shown = SHOWN_10K[community]
    return _points((f"{community} states a fact [{shown[0]}].", 50))


SCRIPTED_REDUCE = _body(f"STATUS: answered\nNVIDIA relies on TSMC [{SUPPLY_CHUNK}].")


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


@pytest.fixture(scope="module")
def graph(neo4j_test_session, chunks):
    from tests.neo4j_fixtures import wipe_except_marker

    wipe_except_marker(neo4j_test_session)
    apply_constraints(neo4j_test_session)
    write_batch(neo4j_test_session, community_batch(graph_local_batch(*chunks)))
    return neo4j_test_session


def _arm(corpus, graph, tmp_path, model, *, similarities=SIMILARITIES, **kwargs):
    return GraphGlobalArm(corpus, graph, FakeQueryEmbedder(QUERY_VECTOR),
                          report_cache(tmp_path / "reports", similarities), model,
                          reports_file(), load_reports=load_reports_dir(tmp_path), **kwargs)


def _ids(result) -> list[str]:
    return [r.chunk_id for r in result.retrieved]


# ── recorded replies ─────────────────────────────────────────────────────────

def test_the_recordings_were_made_with_the_current_prompts():
    for name in QUESTIONS:
        fixture = recorded(name)
        assert fixture["question"] == QUESTIONS[name]
        assert fixture["map_prompt"] == load_map_prompt().prompt_version
        assert fixture["reduce_prompt"] == load_reduce_prompt().prompt_version


@pytest.mark.parametrize("name", sorted(QUESTIONS))
def test_a_recorded_question_maps_three_communities_and_reduces_to_a_cited_answer(
        corpus, graph, tmp_path, name):
    model = RecordedGlobalModel(name)
    result = _arm(corpus, graph, tmp_path, model).run(QUESTIONS[name])
    search = result.graph.global_search
    # The filter keeps the two 10-Ks: the 10-Q's chunks are never shown.
    assert {c.community_id: c.shown for c in search.calls} == SHOWN_10K
    assert [c.rank for c in search.calls] == [1, 2, 3]
    assert _ids(result) == RANKED
    assert [s.chunk_id for s in result.sources] == RANKED
    assert all(c.outcome == "ok" for c in search.calls)
    for call in search.calls:
        assert all(set(p.citations) <= set(call.shown) for p in call.points if p.kept)
    assert search.reduce_called and search.reduce_points > 0
    assert len(model.requests) == 4
    assert result.answer.status in ("answered", "not_found")
    assert result.answer.citation_check.kept
    assert all(set(s.citations) <= set(RANKED) for s in result.answer.citation_check.kept)
    assert result.answer.from_cache
    assert list(result.graph.stages) == ["community_select", "global_map"]
    assert result.graph.stages["global_map"].cost_usd > 0
    assert result.graph.stages["global_map"].from_cache


# ── scripted replies ─────────────────────────────────────────────────────────

def test_a_question_whose_filter_leaves_no_community_is_not_found_with_no_call(
        corpus, graph, tmp_path):
    model = ScriptedGlobalModel(_supply_points)
    result = _arm(corpus, graph, tmp_path, model).run(AMD_FY2025)
    assert model.requests == []
    assert (result.answer.status, result.answer.text) == ("not_found", NOT_FOUND_TEXT)
    assert result.retrieved == () and result.sources == ()
    assert result.graph.global_search.calls == ()
    assert list(result.graph.stages) == ["community_select"]


def test_a_point_citing_a_chunk_its_call_was_not_shown_is_dropped(corpus, graph, tmp_path):
    maps = {
        "lower-blackwell": _points((f"Blackwell shipped [{Q_BLACKWELL_CHUNK}].", 90),
                                   (f"Blackwell led revenue [{K_BLACKWELL_CHUNK}].", 80)),
        "lower-export": _points((f"Export rules [{SUPPLY_CHUNK}].", 95),  # another call's chunk
                                ("No citation at all.", 85)),
        "lower-supply": _points((f"TSMC makes wafers [{SUPPLY_CHUNK}].", 70)),
    }
    model = ScriptedGlobalModel(maps, SCRIPTED_REDUCE)
    result = _arm(corpus, graph, tmp_path, model).run(QUESTION)
    reasons = {p.text: p.drop_reason for p in result.graph.global_search.points}
    assert reasons == {
        f"Blackwell shipped [{Q_BLACKWELL_CHUNK}].": "citation_not_shown",
        f"Blackwell led revenue [{K_BLACKWELL_CHUNK}].": None,
        f"Export rules [{SUPPLY_CHUNK}].": "citation_not_shown",
        "No citation at all.": "no_citation",
        f"TSMC makes wafers [{SUPPLY_CHUNK}].": None,
    }
    (reduce,) = model.reduce_requests
    assert "Blackwell shipped" not in reduce.user and "Export rules" not in reduce.user
    assert [p.text for p in result.graph.global_search.points if p.in_reduce] == [
        f"Blackwell led revenue [{K_BLACKWELL_CHUNK}].", f"TSMC makes wafers [{SUPPLY_CHUNK}]."]


def test_the_reduce_reads_points_by_score_then_rank_within_its_budget(corpus, graph, tmp_path):
    maps = {
        "lower-blackwell": _points((f"B-low [{K_BLACKWELL_CHUNK}].", 30)),
        "lower-export": _points((f"E-top [{K_RISK_CHUNK}].", 90),
                                (f"E-mid [{EXPORT_CHUNK}].", 60)),
        "lower-supply": _points((f"S-mid [{SUPPLY_CHUNK}].", 60),
                                (f"S-zero [{SEGMENTS_CHUNK}].", 0)),
    }
    model = ScriptedGlobalModel(maps, SCRIPTED_REDUCE)
    result = _arm(corpus, graph, tmp_path, model).run(QUESTION)
    user = model.reduce_requests[0].user
    order = [label for label in ("E-top", "E-mid", "S-mid", "B-low") if label in user]
    assert order == ["E-top", "E-mid", "S-mid", "B-low"]
    assert sorted(order, key=user.index) == order
    assert "S-zero" not in user
    assert result.graph.global_search.cut_by_budget == 0

    capped = ScriptedGlobalModel(maps, SCRIPTED_REDUCE)
    result = _arm(corpus, graph, tmp_path, capped, reduce_budget=1).run(QUESTION)
    capped_user = capped.reduce_requests[0].user
    assert "E-top" in capped_user and "E-mid" not in capped_user
    search = result.graph.global_search
    assert (search.reduce_points, search.cut_by_budget) == (1, 3)


def test_no_point_above_zero_is_not_found_with_no_reduce_call(corpus, graph, tmp_path):
    zero = {c: _points((f"Nothing relevant [{s[0]}].", 0)) for c, s in SHOWN_10K.items()}
    model = ScriptedGlobalModel(zero)
    result = _arm(corpus, graph, tmp_path, model).run(QUESTION)
    assert len(model.map_requests) == 3 and model.reduce_requests == []
    assert (result.answer.status, result.answer.text) == ("not_found", NOT_FOUND_TEXT)
    assert result.answer.cost_usd == 0.0
    assert not result.graph.global_search.reduce_called
    assert _ids(result) == RANKED  # the shown chunks are still the ranked list


def test_a_declined_reduce_shows_the_fixed_sentence(corpus, graph, tmp_path):
    model = ScriptedGlobalModel(_supply_points, _body("STATUS: declined\n"))
    result = _arm(corpus, graph, tmp_path, model).run(
        "Which of NVIDIA, AMD or Intel is the best stock to own for AI exposure over the next "
        "five years, according to their 10-Ks?")
    assert (result.answer.status, result.answer.text) == ("declined", DECLINE_TEXT)


def test_the_answer_keeps_sentences_citing_any_shown_chunk_and_drops_the_rest(
        corpus, graph, tmp_path):
    reduce = _body(f"STATUS: answered\nTSMC makes wafers [{SUPPLY_CHUNK}].\n"
                   f"Two segments [{SEGMENTS_CHUNK}].\nOfficers [{OFFICERS_CHUNK}].\nUncited.")
    model = ScriptedGlobalModel(_supply_points, reduce)
    result = _arm(corpus, graph, tmp_path, model).run(QUESTION)
    check = result.answer.citation_check
    # SEGMENTS was shown to a map call but sent to the reduce in no point: still allowed.
    assert [s.text for s in check.kept] == [f"TSMC makes wafers [{SUPPLY_CHUNK}].",
                                            f"Two segments [{SEGMENTS_CHUNK}]."]
    assert [(d.reason) for d in check.dropped] == ["citation_not_retrieved", "no_citation"]


def test_an_unusable_map_reply_gives_its_community_no_points(corpus, graph, tmp_path):
    maps = {"lower-blackwell": _body("not json"),
            "lower-export": {**_points((f"E [{K_RISK_CHUNK}].", 90)), "stop_reason": "refusal"},
            "lower-supply": _points((f"S [{SUPPLY_CHUNK}].", 60))}
    result = _arm(corpus, graph, tmp_path, ScriptedGlobalModel(maps, SCRIPTED_REDUCE)).run(QUESTION)
    calls = {c.community_id: (c.outcome, len(c.points)) for c in result.graph.global_search.calls}
    assert calls == {"lower-blackwell": ("invalid_reply", 0), "lower-export": ("refused", 0),
                     "lower-supply": ("ok", 1)}
    assert graph_cell([result])["map_calls_unusable"] == 2


def test_stage_costs_sum_the_map_calls(corpus, graph, tmp_path):
    model = ScriptedGlobalModel(_supply_points, SCRIPTED_REDUCE)
    result = _arm(corpus, graph, tmp_path, model).run(QUESTION)
    calls = result.graph.global_search.calls
    stage = result.graph.stages["global_map"]
    assert stage.cost_usd == pytest.approx(sum(c.cost.cost_usd for c in calls))
    assert stage.cost_usd > 0
    assert stage.input_tokens == sum(c.cost.input_tokens for c in calls)
    assert all(c.cost.ms == pytest.approx(1000.0) for c in calls)
    assert result.answer.generation_ms == pytest.approx(3000.0)
    assert result.answer.cost_usd > 0
    assert result.search_ms >= result.graph.stages["community_select"].ms


def test_the_question_record_holds_every_map_point_and_the_reduce_input(corpus, graph, tmp_path):
    model = ScriptedGlobalModel(_supply_points, SCRIPTED_REDUCE)
    record = question_graph(_arm(corpus, graph, tmp_path, model).run(QUESTION).graph)
    search = record["global_search"]
    assert [c["community_id"] for c in search["map_calls"]] == list(SHOWN_10K)
    assert search["map_calls"][0]["points"][0]["in_reduce"] is True
    assert search["reduce"]["called"] is True and search["reduce"]["input_tokens"] > 0
    assert set(record["stages"]) == {"community_select", "global_map"}


# ── failures and refusals ────────────────────────────────────────────────────

def test_a_failed_map_call_is_an_arm_error(corpus, graph, tmp_path):
    model = ScriptedGlobalModel(_supply_points, error=AnswerModelError("HTTP 529"))
    with pytest.raises(ArmError, match="graph_global arm failed.*529"):
        _arm(corpus, graph, tmp_path, model).run(QUESTION)


def test_a_graph_at_ontology_version_4_stops_the_arm(corpus, graph, tmp_path):
    graph.execute_query("MATCH (m:GraphMeta) SET m.ontology_version = 4")
    try:
        with pytest.raises(ArmError, match="ontology"):
            _arm(corpus, graph, tmp_path, ScriptedGlobalModel(_supply_points))
    finally:
        graph.execute_query("MATCH (m:GraphMeta) SET m.ontology_version = 5")


def test_a_graph_with_another_schema_hash_stops_the_arm(corpus, graph, tmp_path):
    records, _, _ = graph.execute_query("MATCH (m:GraphMeta) RETURN m.schema_sha256 AS sha")
    graph.execute_query("MATCH (m:GraphMeta) SET m.schema_sha256 = 'another schema'")
    try:
        with pytest.raises(ArmError, match="ontology"):
            _arm(corpus, graph, tmp_path, ScriptedGlobalModel(_supply_points))
    finally:
        graph.execute_query("MATCH (m:GraphMeta) SET m.schema_sha256 = $sha",
                            sha=records[0]["sha"])


def test_an_uncached_report_text_stops_the_arm(corpus, graph, tmp_path):
    partial = {c: s for c, s in SIMILARITIES.items() if c != "lower-supply"}
    with pytest.raises(ArmError, match="report texts have no cached"):
        _arm(corpus, graph, tmp_path, ScriptedGlobalModel(_supply_points), similarities=partial)


def test_an_unpriced_model_stops_the_arm(corpus, graph, tmp_path):
    with pytest.raises(ArmError, match="no price"):
        _arm(corpus, graph, tmp_path, ScriptedGlobalModel(_supply_points),
             embedding_model="voyage-unpriced")


# ── config, spans and registration ───────────────────────────────────────────

def test_config_pins_the_settings_prompts_and_files(corpus, graph, tmp_path):
    arm = _arm(corpus, graph, tmp_path, ScriptedGlobalModel(_supply_points))
    config = arm.config
    assert dict(config.models) == {"embedding": MODEL, "map": "claude-sonnet-5-5",
                                   "reduce": "claude-sonnet-5-5"}
    assert dict(config.efforts) == {"map": "high", "reduce": "high"}
    assert config.answer_prompt.version == load_reduce_prompt().prompt_version
    header = header_graph(config.graph)
    assert header["load_run"] == "load-run5"
    assert header["global_search"] | {} == {
        "n": 4, "level": "lower", "chunks_per_community": 6, "reduce_budget_tokens": 8000,
        "map_workers": 4,
        "map_prompt_version": load_map_prompt().prompt_version,
        "map_prompt_sha256": load_map_prompt().sha256,
        "reduce_prompt_version": load_reduce_prompt().prompt_version,
        "reduce_prompt_sha256": load_reduce_prompt().sha256,
        "report_prompt_versions": ("report/v2@3062cfff",),
        "communities_file": "communities-run1.json", "reports_file": "reports-run1.jsonl"}


def _spans(capture_text: bool):
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, SpanRecorder(provider.get_tracer("test"), capture_text=capture_text)


def test_spans_show_the_fan_out_with_no_prompt_text(corpus, graph, tmp_path):
    exporter, spans = _spans(capture_text=False)
    model = ScriptedGlobalModel(_supply_points, SCRIPTED_REDUCE)
    _arm(corpus, graph, tmp_path, model, spans=spans).run(QUESTION)
    finished = exporter.get_finished_spans()
    assert sorted(s.name for s in finished) == sorted(
        ["question_filter", "query_embedding", "community_select", "global_map", "global_map",
         "global_map", "global_reduce"])
    maps = [s for s in finished if s.name == "global_map"]
    assert {s.attributes[COMMUNITY_ATTR] for s in maps} == set(SHOWN_10K)
    assert all(s.attributes["filingintel.cost_usd"] > 0 for s in maps)
    assert not [k for s in finished for k in s.attributes if k.startswith("llm.input_messages")]


def test_text_capture_puts_the_prompts_on_the_map_and_reduce_spans(corpus, graph, tmp_path):
    exporter, spans = _spans(capture_text=True)
    model = ScriptedGlobalModel(_supply_points, SCRIPTED_REDUCE)
    _arm(corpus, graph, tmp_path, model, spans=spans).run(QUESTION)
    for span in exporter.get_finished_spans():
        if span.name in ("global_map", "global_reduce"):
            assert [k for k in span.attributes if k.startswith("llm.input_messages")]


def test_the_arm_is_registered_with_the_settings_it_needs():
    spec = ARMS["graph_global"]
    assert set(spec.required_env) == {"NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
                                      "DATABASE_URL", "VOYAGE_API_KEY", "ANTHROPIC_API_KEY"}


def test_a_question_cost_lists_the_global_stages_only(corpus, graph, tmp_path):
    from eval.operational import question_cost

    model = ScriptedGlobalModel(_supply_points, SCRIPTED_REDUCE)
    result = _arm(corpus, graph, tmp_path, model).run(QUESTION)
    cost = question_cost(result)
    assert list(cost) == ["embedding", "community_select", "global_map", "generation", "total"]
    assert cost["total"] == pytest.approx(
        cost["embedding"] + cost["global_map"] + result.answer.cost_usd)
