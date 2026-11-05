"""The UI's per-session spend cap (Step 14 ticket 01).

Only calls made now count: a replayed answer, embedding, graph call or map call
adds nothing.  Benchmark spend files are never read or written here.
"""
from dataclasses import replace
from types import MappingProxyType

import pytest

from retrieve.arm import GlobalSearch, GraphRetrieval, MapCall, StageCost
from tests.ui_fakes import arm_result, routing
from ui.spend import (
    DEFAULT_MAX_SPEND,
    SpendCapReached,
    SpendConfigError,
    check_spend,
    fresh_cost,
    max_spend,
)


def test_a_fresh_answer_costs_its_embedding_stages_routing_and_generation():
    result = arm_result(embed_cost=0.001, answer_cost=0.02, graph=GraphRetrieval(
        stages=MappingProxyType({"cypher_generation": StageCost(ms=1, cost_usd=0.01)})),
        routing=routing(cost=0.0002))
    assert fresh_cost(result) == pytest.approx(0.001 + 0.01 + 0.0002 + 0.02)


def test_a_replayed_answer_adds_nothing():
    result = arm_result(embed_cost=0.001, answer_cost=0.02, query_cached=True,
                        answer_cached=True, graph=GraphRetrieval(stages=MappingProxyType(
                            {"cypher_generation": StageCost(ms=1, cost_usd=0.01,
                                                            from_cache=True)})),
                        routing=routing(cost=0.0002, from_cache=True))
    assert fresh_cost(result) == 0.0


def test_map_calls_count_one_by_one_not_through_their_stage():
    calls = (_call(0.03, from_cache=False), _call(0.05, from_cache=True))
    graph = GraphRetrieval(
        stages=MappingProxyType({"global_map": StageCost(ms=1, cost_usd=0.08)}),
        global_search=GlobalSearch(calls=calls, reduce_points=1, reduce_input_tokens=10,
                                   cut_by_budget=0))
    result = arm_result(query_cached=True, answer_cached=True, graph=graph)
    assert fresh_cost(result) == pytest.approx(0.03)


def _call(usd, *, from_cache):
    return MapCall("lower-1", 1, 0.5, (), "ok", (),
                   StageCost(ms=1, cost_usd=usd, from_cache=from_cache))


def test_the_cap_refuses_when_the_estimate_would_pass_it():
    check_spend(estimate=0.3, spent=0.7, cap=1.0)  # exactly at the cap is allowed
    with pytest.raises(SpendCapReached, match=r"\$0\.31.*\$0\.70.*\$1\.00"):
        check_spend(estimate=0.31, spent=0.7, cap=1.0)


def test_the_cap_comes_from_the_environment_with_a_default():
    assert max_spend({}) == DEFAULT_MAX_SPEND == 1.0
    assert max_spend({"FILINGINTEL_UI_MAX_SPEND": " 0.25 "}) == 0.25
    for bad in ("lots", "-1", "nan", "inf"):
        with pytest.raises(SpendConfigError, match="FILINGINTEL_UI_MAX_SPEND"):
            max_spend({"FILINGINTEL_UI_MAX_SPEND": bad})


def test_a_retrieval_with_no_graph_or_routing_costs_embedding_and_answer():
    result = replace(arm_result(embed_cost=0.002, answer_cost=0.01), graph=None, routing=None)
    assert fresh_cost(result) == pytest.approx(0.012)
