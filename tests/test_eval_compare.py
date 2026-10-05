"""``python -m eval.compare``: the score change between two results files (Step 5, ticket 08).

Both files are written by ``eval.run`` with the fake arm and fake judges from
``test_eval_run``, the second with ``--uncached``, so they have the real shape.
"""
import copy
import json

import pytest

from eval.compare import EXIT_USAGE, CompareError, compare, main
from tests.test_eval_run import _setup, env, eval_dir  # noqa: F401 (fixtures)


@pytest.fixture
def two_runs(eval_dir, tmp_path, env):  # noqa: F811 (pytest fixtures imported above)
    _, _, runs, run = _setup(eval_dir, tmp_path)
    assert run() == 0
    assert run("--uncached") == 0
    first, second = sorted(runs.glob("*.json"))
    return first, second


def _load(path):
    return json.loads(path.read_text())


def test_two_runs_with_equal_scores_change_by_zero_everywhere(two_runs):
    before, after = (_load(p) for p in two_runs)
    changes = compare(before, after)
    assert changes and all(c.delta == 0 for c in changes)
    metrics = {c.metric for c in changes if c.class_ == "lookup"}
    assert {"metrics.recall@5", "wrong_evidence.rate",
            "answers.structural_citation_validity"} <= metrics
    assert not any("interval" in c.metric or "resamples" in c.metric for c in changes)


def test_a_changed_score_is_reported_as_after_minus_before(two_runs):
    before, after = (_load(p) for p in two_runs)
    after["cells"]["agent_drafted"]["fake"]["lookup"]["metrics"]["recall@5"] = 0.75
    (change,) = [c for c in compare(before, after)
                 if (c.class_, c.metric) == ("lookup", "metrics.recall@5")]
    assert change.question_set == "agent_drafted"
    assert (change.before, change.after) == (0.5, 0.75)
    assert change.delta == pytest.approx(0.25)


def test_a_metric_in_only_one_file_has_no_delta(two_runs):
    before, after = (_load(p) for p in two_runs)
    del after["cells"]["agent_drafted"]["fake"]["global"]["metrics"]["recall@10"]
    (change,) = [c for c in compare(before, after)
                 if (c.class_, c.metric) == ("global", "metrics.recall@10")]
    assert change.after is None and change.delta is None


@pytest.mark.parametrize("field, value", [
    ("question_sets", [{"name": "agent_drafted", "sha256": "0" * 64}]),
    ("answer_prompt", {"version": "v2", "sha256": "0" * 64}),
    ("split", "test"),
])
def test_runs_with_a_different_setup_are_refused(two_runs, field, value):
    before, after = (_load(p) for p in two_runs)
    after["header"][field] = value
    with pytest.raises(CompareError, match=field):
        compare(before, after)


def test_runs_that_differ_only_in_commit_time_and_cache_are_compared(two_runs):
    before, after = (_load(p) for p in two_runs)
    assert before["header"]["response_cache"] != after["header"]["response_cache"]
    changed = copy.deepcopy(after)
    changed["header"]["commit"] = "abcdef0"
    assert compare(before, changed)


def test_two_arms_are_compared_class_by_class(two_runs):
    before, after = (_load(p) for p in two_runs)
    after["header"].update(arm="graph", models={"answer": "another-model"}, k=20)
    after["cells"]["agent_drafted"] = {"graph": after["cells"]["agent_drafted"].pop("fake")}
    after["cells"]["agent_drafted"]["graph"]["lookup"]["metrics"]["recall@5"] = 1.0
    (change,) = [c for c in compare(before, after)
                 if (c.class_, c.metric) == ("lookup", "metrics.recall@5")]
    assert change.delta == pytest.approx(0.5)


@pytest.mark.parametrize("cells", [
    {"agent_drafted": ["not", "arms"]},
    {"agent_drafted": {"fake": {"lookup": "not a cell"}}},
    {"agent_drafted": {"fake": "no classes"}},
])
def test_malformed_cells_are_refused(two_runs, cells):
    before, after = (_load(p) for p in two_runs)
    after["cells"] = cells
    with pytest.raises(CompareError, match="cells"):
        compare(before, after)


def test_the_command_prints_both_runs_and_every_change(two_runs, capsys):
    assert main([str(p) for p in two_runs]) == 0
    out = capsys.readouterr().out
    assert two_runs[0].name in out and two_runs[1].name in out
    assert "metrics.recall@5" in out


def test_the_command_refuses_a_file_that_is_not_json(two_runs, tmp_path, capsys):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert main([str(two_runs[0]), str(broken)]) == EXIT_USAGE
    assert "line 1" in capsys.readouterr().err


def test_the_command_refuses_a_missing_file(two_runs, tmp_path, capsys):
    assert main([str(two_runs[0]), str(tmp_path / "missing.json")]) == EXIT_USAGE
    assert "missing.json" in capsys.readouterr().err
