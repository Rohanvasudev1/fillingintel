"""graph_global's map and reduce steps, pure (Step 12 ticket 06, ADR-0008).

The prompts are frozen by SHA-256; a map reply's points are checked against
the chunks its call was shown; the reduce input takes points above 0 by score,
then community rank, under a token budget.  Chunk IDs are real fixture IDs.
"""
import json
import shutil

import pytest

from ingest.chunker import count_tokens
from retrieve.answer_prompt import SourceChunk
from retrieve.arm import MapCall, MapPoint, StageCost
from retrieve.global_map import build_map_request, read_map_reply
from retrieve.global_prompts import (
    MAP_PROMPTS_DIR,
    PromptError,
    load_map_prompt,
    load_reduce_prompt,
)
from retrieve.global_reduce import build_reduce_request, point_line, select_points
from tests.anthropic_fixtures import load, with_text

NVDA_10K = "0001045810-26-000021"
SUPPLY, SEGMENTS, EXPORT = f"{NVDA_10K}:0012", f"{NVDA_10K}:0006", f"{NVDA_10K}:0013"
CHUNKS = (
    SourceChunk(SUPPLY, "NVDA", "10-K", "FY2026", "part_i_item_1a",
                "We depend on TSMC to manufacture our wafers."),
    SourceChunk(SEGMENTS, "NVDA", "10-K", "FY2026", "part_i_item_1",
                "We report two segments, Compute & Networking and Graphics."),
)
SHOWN = frozenset({SUPPLY, SEGMENTS})


def _body(points) -> dict:
    return with_text(load("answered_q0072")["response"], json.dumps({"points": points}))


def _read(points, **changes):
    body = {**_body(points), **changes}
    return read_map_reply(body, SHOWN, "lower-supply")


# ── prompts ───────────────────────────────────────────────────────────────────

def test_both_prompts_load_with_versions_that_cover_what_is_sent():
    map_prompt, reduce_prompt = load_map_prompt(), load_reduce_prompt()
    assert map_prompt.prompt_version.startswith("global_map/v1@")
    assert reduce_prompt.prompt_version.startswith("global_reduce/v1@")
    assert map_prompt.path == "prompts/global_map/v1.md"
    assert "# User" not in map_prompt.system and "$question" in map_prompt.user_template
    assert "STATUS: declined" in reduce_prompt.system


def test_an_edited_map_prompt_is_refused(tmp_path):
    shutil.copy(MAP_PROMPTS_DIR / "v1.md", tmp_path / "v1.md")
    with open(tmp_path / "v1.md", "a", encoding="utf-8") as fh:
        fh.write("Be brief.\n")
    with pytest.raises(PromptError, match="global_map v1 has changed"):
        load_map_prompt("v1", tmp_path)


def test_an_unpublished_reduce_prompt_is_refused():
    with pytest.raises(PromptError, match="v9"):
        load_reduce_prompt("v9")


# ── the map request and reply ───────────────────────────────────────────────

def test_the_map_request_shows_the_report_without_its_citations_and_every_chunk():
    report = f"TSMC makes NVIDIA's wafers [{EXPORT}].\n- A finding [{SUPPLY}]."
    request = build_map_request(load_map_prompt(), "Who makes the wafers?", "TSMC supply",
                                report, CHUNKS)
    params = request.params()
    assert (params["model"], params["output_config"]["effort"]) == ("claude-sonnet-5-5", "high")
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in params
    user = params["messages"][0]["content"]
    assert EXPORT not in user  # the report's own citation is stripped
    assert "TSMC makes NVIDIA's wafers." in user
    assert f'<excerpt id="{SUPPLY}"' in user and f'<excerpt id="{SEGMENTS}"' in user
    assert user.rstrip().endswith("Question: Who makes the wafers?")
    assert request.cache_key() != build_map_request(
        load_map_prompt(), "Another question?", "TSMC supply", report, CHUNKS).cache_key()


def test_points_citing_only_shown_chunks_are_kept_and_others_dropped_with_a_reason():
    reply = _read([
        {"text": f"TSMC makes the wafers [{SUPPLY}].", "score": 90},
        {"text": f"Export rules apply [{EXPORT}].", "score": 70},
        {"text": f"Two segments [{SEGMENTS}] and exports [{EXPORT}].", "score": 60},
        {"text": "NVIDIA has no fabs.", "score": 50},
        {"text": f"Out of range [{SUPPLY}].", "score": 101},
        {"text": f"Not a number [{SUPPLY}].", "score": True},
        {"text": f"Irrelevant [{SEGMENTS}].", "score": 0},
    ])
    assert reply.outcome == "ok"
    assert [(p.score, p.drop_reason) for p in reply.points] == [
        (90, None), (70, "citation_not_shown"), (60, "citation_not_shown"),
        (50, "no_citation"), (None, "bad_score"), (None, "bad_score"), (0, None)]
    assert reply.points[0].citations == (SUPPLY,)
    assert {p.community_id for p in reply.points} == {"lower-supply"}
    assert reply.usage.output_tokens > 0


@pytest.mark.parametrize(("changes", "outcome"), [
    ({"stop_reason": "max_tokens"}, "max_tokens"),
    ({"stop_reason": "refusal"}, "refused"),
])
def test_a_cut_off_or_refused_reply_gives_no_points(changes, outcome):
    reply = _read([{"text": f"TSMC [{SUPPLY}].", "score": 90}], **changes)
    assert (reply.outcome, reply.points) == (outcome, ())


@pytest.mark.parametrize("text", ["not json", json.dumps({"points": "none"}),
                                  json.dumps({"points": [{"text": 3, "score": 1}]}),
                                  json.dumps({"items": []})])
def test_a_reply_off_the_schema_is_invalid_with_no_points(text):
    body = with_text(load("answered_q0072")["response"], text)
    reply = read_map_reply(body, SHOWN, "lower-supply")
    assert (reply.outcome, reply.points) == ("invalid_reply", ())


# ── the reduce input ─────────────────────────────────────────────────────────

def _point(community: str, text: str, score, reason=None) -> MapPoint:
    return MapPoint(community, text, score, (SUPPLY,) if reason is None else (), reason)


def _call(community: str, rank: int, *points: MapPoint) -> MapCall:
    return MapCall(community, rank, 0.5, (SUPPLY,), "ok", points, StageCost(ms=1.0))


def test_reduce_takes_points_above_zero_by_score_then_community_rank():
    calls = (
        _call("lower-a", 1, _point("lower-a", "a1", 40), _point("lower-a", "a2", 80),
              _point("lower-a", "a0", 0)),
        _call("lower-b", 2, _point("lower-b", "b1", 80), _point("lower-b", "b2", 95),
              _point("lower-b", "bx", 99, "no_citation")),
    )
    selected = select_points(calls, budget=8_000)
    assert [p.text for p in selected.points] == ["b2", "a2", "b1", "a1"]
    assert (selected.over_zero, selected.cut_by_budget) == (4, 0)
    assert selected.tokens > 0


def test_reduce_stops_adding_points_at_the_token_budget():
    long = " ".join(["word"] * 60) + f" [{SUPPLY}]."
    points = [_point("lower-a", f"{i} {long}", 90 - i) for i in range(5)]
    budget = 2 * count_tokens(point_line(points[0])) + 1  # room for two points
    selected = select_points((_call("lower-a", 1, *points),), budget=budget)
    assert [p.score for p in selected.points] == [90, 89]
    assert selected.cut_by_budget == 3
    assert selected.tokens <= budget


def test_the_first_point_is_kept_even_when_it_alone_exceeds_the_budget():
    selected = select_points((_call("lower-a", 1, _point("lower-a", "a long point", 50)),),
                             budget=1)
    assert [p.text for p in selected.points] == ["a long point"]


def test_no_point_above_zero_selects_nothing():
    selected = select_points((_call("lower-a", 1, _point("lower-a", "a0", 0)),), budget=8_000)
    assert (selected.points, selected.over_zero) == ((), 0)


def test_the_reduce_request_lists_the_points_in_order_then_the_question():
    points = (_point("lower-b", f"First [{SUPPLY}].", 95), _point("lower-a", "Second.", 40))
    request = build_reduce_request(load_reduce_prompt(), "Who makes the wafers?", points)
    assert (request.model, request.effort) == ("claude-sonnet-5-5", "high")
    assert request.user.index("First") < request.user.index("Second")
    assert 'score="95"' in request.user
    assert request.user.rstrip().endswith("Question: Who makes the wafers?")
    assert request.system == load_reduce_prompt().system
