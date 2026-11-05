"""A sample's committed dev metrics and each option's cost estimate (Step 14 ticket 01).

Read from the committed run files, so the numbers are the ones results.md uses.
"""
import json
from statistics import fmean

import pytest

from ingest.provenance import REPO_ROOT
from ui.committed import RUNS_DIR, mean_dev_cost, sample_metrics
from ui.options import OPTIONS, picker_option


def _questions(name):
    return json.loads((RUNS_DIR / name).read_text())["questions"]


def test_a_sample_shows_its_record_from_the_options_dev_run():
    option = picker_option("graph_local")
    record = next(q for q in _questions(option.dev_runs[0]) if q["class"] == "lookup")
    found = sample_metrics(option, record["id"])
    assert found.run_file == option.dev_runs[0]
    assert found.class_ == "lookup"
    assert dict(found.retrieval) == record["metrics"]
    assert found.judged["faithfulness"] == record["judged"]["metrics"]["faithfulness"]["mean"]
    assert found.judged_label == "uncalibrated"
    assert found.wrong_evidence is (len(record["wrong_evidence"]) > 0)


def test_graph_global_reads_its_full_run_first_then_the_retrieval_only_one():
    option = picker_option("graph_global")
    full, retrieval_only = option.dev_runs
    global_q = _questions(full)[0]["id"]
    assert sample_metrics(option, global_q).run_file == full
    lookup = next(q for q in _questions(retrieval_only) if q["class"] == "lookup")
    found = sample_metrics(option, lookup["id"])
    assert found.run_file == retrieval_only
    assert found.judged == {}


def test_a_question_no_dev_run_scored_has_no_metrics():
    assert sample_metrics(picker_option("vector"), "q9999") is None


def test_the_estimate_is_the_mean_answer_cost_per_question_without_judges():
    option = picker_option("vector")
    costs = [q["cost_usd"]["total"] for q in _questions(option.dev_runs[0])]
    assert mean_dev_cost(option) == pytest.approx(fmean(costs))


def test_graph_global_estimates_only_from_questions_it_answered():
    option = picker_option("graph_global")
    costs = [q["cost_usd"]["total"] for q in _questions(option.dev_runs[0])]
    assert mean_dev_cost(option) == pytest.approx(fmean(costs))


def test_every_option_has_a_positive_estimate():
    for option in OPTIONS:
        assert 0 < mean_dev_cost(option) < 1.0


def test_runs_dir_is_the_committed_one():
    assert RUNS_DIR == REPO_ROOT / "benchmarks" / "runs"
