"""Step 13's spend from committed files (Step 13 ticket 03): route runs count their fresh
calls; router and test runs count each call not replayed, with a question's judges
when its answer was fresh; recorded router test replies count in full."""
import json

import pytest

from eval.step13_spend import spent_usd


def _question(answer_cached: bool, routing_cached: bool | None = None,
              query_cached: bool = True) -> dict:
    question = {
        "query_cached": query_cached,
        "cost_usd": {"embedding": 0.001, "generation": 0.02, "total": 0.021},
        "answer": {"from_cache": answer_cached},
        "judged": {"cost_usd": 0.01},
    }
    if routing_cached is not None:
        question["routing"] = {"cost": {"cost_usd": 0.0005, "from_cache": routing_cached}}
        question["cost_usd"] = {**question["cost_usd"], "routing": 0.0005, "total": 0.0215}
    return question


def _run(tmp_path, name: str, header: dict, questions: list[dict]) -> None:
    runs = tmp_path / "runs"
    runs.mkdir(exist_ok=True)
    (runs / name).write_text(json.dumps({"header": header, "questions": questions}))


def test_spend_sums_fresh_calls_only(tmp_path):
    router_dir = tmp_path / "router"
    router_dir.mkdir()
    (router_dir / "route-run1.json").write_text(json.dumps({"spent_usd": 0.05}))
    _run(tmp_path, "2026-10-18-router-pre_registered-dev-abc.json",
         {"arm": "router", "split": "dev", "mode": "full"},
         [_question(answer_cached=True, routing_cached=True),
          _question(answer_cached=False, routing_cached=False, query_cached=False)])
    _run(tmp_path, "2026-10-14-graph_local-dev-375efac.json",  # before Step 13: not counted
         {"arm": "graph_local", "split": "dev", "mode": "full"}, [_question(False)])
    _run(tmp_path, "2026-10-19-vector-test-abc.json", {"arm": "vector", "split": "test",
                                                        "mode": "full"}, [_question(False)])
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    expected = 0.05 + (0.0005 + 0.001 + 0.02 + 0.01) + (0.02 + 0.01)
    assert spent_usd(router_dir, tmp_path / "runs", fixtures) == pytest.approx(expected)


def test_an_unreadable_file_is_named(tmp_path):
    router_dir = tmp_path / "router"
    router_dir.mkdir()
    (router_dir / "route-run1.json").write_text("{")
    with pytest.raises(ValueError, match="route-run1.json"):
        spent_usd(router_dir, tmp_path / "runs", tmp_path)
