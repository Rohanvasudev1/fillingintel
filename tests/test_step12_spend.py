"""Step 12's spend over reports runs and graph_global runs (Step 12 ticket 07)."""
import json

import pytest

from graph.step12_spend import spent_usd


def _reports_run(directory, n: int, cost: float) -> None:
    (directory / f"reports-run{n}.jsonl").write_text(
        json.dumps({"cost_this_run_usd": cost}) + "\n")


def _run_file(directory, name: str, arm: str, totals: list[float], judges: float) -> None:
    (directory / name).write_text(json.dumps({
        "header": {"arm": arm},
        "questions": [{"cost_usd": {"total": t}} for t in totals],
        "judge_usage": {"cost_usd": judges},
    }))


@pytest.fixture
def dirs(tmp_path):
    communities, runs = tmp_path / "communities", tmp_path / "runs"
    communities.mkdir()
    runs.mkdir()
    return communities, runs


def test_graph_global_runs_add_their_questions_and_judges(dirs):
    communities, runs = dirs
    _reports_run(communities, 1, 0.5)
    _run_file(runs, "2026-10-16-graph_global-dev-abc1234.json", "graph_global", [0.25, 0.5], 0.125)
    _run_file(runs, "2026-10-05-vector-dev-96e8c69.json", "vector", [9.0], 9.0)
    assert spent_usd(communities, runs) == pytest.approx(1.375)


def test_a_graph_global_run_without_judge_usage_cannot_be_summed(dirs):
    communities, runs = dirs
    (runs / "2026-10-16-graph_global-dev-abc1234.json").write_text(
        json.dumps({"header": {"arm": "graph_global"}, "questions": []}))
    with pytest.raises(ValueError, match="2026-10-16-graph_global-dev-abc1234.json"):
        spent_usd(communities, runs)


def test_a_retrieval_only_graph_global_run_spends_nothing(dirs):
    communities, runs = dirs
    _reports_run(communities, 1, 0.5)
    (runs / "2026-10-17-graph_global-dev-6e26741.json").write_text(json.dumps({
        "header": {"arm": "graph_global", "mode": "retrieval_only"},
        "questions": [{"id": "q0072"}]}))  # no cost or judge fields: no model call
    assert spent_usd(communities, runs) == pytest.approx(0.5)


def test_the_committed_run_files_can_be_summed():
    assert spent_usd() >= 0  # every file under benchmarks/runs reads, retrieval-only included
