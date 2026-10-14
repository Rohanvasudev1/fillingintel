"""``python -m eval.compare``: the score change between two results files (Step 5, ticket 08).

Both files are written by ``eval.run`` with the fake arm and fake judges from
``test_eval_run``, the second with ``--uncached``, so they have the real shape.
"""
import copy
import json

import pytest

from eval.compare import EXIT_USAGE, POOLED, CompareError, compare, compare_paired, main
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


# Paired differences (Step 11, ticket 03).  The small run files below hold only
# what the paired comparison reads: the header fields it checks and the
# per-question records.

SET = "agent_drafted"


def _question(qid, cls, *, recall=None, wrong=None, faithfulness=None, question_set=SET):
    return {
        "id": qid,
        "question_set": question_set,
        "class": cls,
        "metrics": {} if recall is None else {"recall@5": recall, "precision@5": recall / 5},
        "wrong_evidence": None if wrong is None else ([{"chunk_id": "x"}] if wrong else []),
        "judged": None if faithfulness is None else {
            "label": "uncalibrated",
            "metrics": {"faithfulness": {"runs": [faithfulness], "mean": faithfulness}},
        },
    }


def _run(questions, arm="vector"):
    return {
        "header": {"arm": arm, "commit": "abc1234", "split": "dev", "seed": 7,
                   "answer_prompt": {"version": "v1", "sha256": "a" * 64},
                   "question_sets": [{"name": SET, "sha256": "b" * 64}]},
        "cells": {},
        "questions": questions,
    }


def _difference(paired, class_, metric):
    (found,) = [d for d in paired.differences if (d.class_, d.metric) == (class_, metric)]
    return found


def test_identical_runs_have_no_counted_paired_difference(two_runs):
    before, after = (_load(p) for p in two_runs)
    paired = compare_paired(before, after)
    assert paired.differences
    assert all(d.mean == 0 and (d.low, d.high) == (0, 0) and not d.counts
               for d in paired.differences)
    lookup = {d.metric for d in paired.differences if d.class_ == "lookup"}
    assert {"metrics.recall@5", "metrics.recall@10", "metrics.precision@5",
            "metrics.precision@10", "wrong_evidence.rate"} <= lookup
    judged = [d for d in paired.differences if d.metric.startswith("judged.")]
    assert judged and all(d.label == "uncalibrated" for d in judged)
    assert {d.class_ for d in paired.differences} >= {"lookup", POOLED}
    assert all(d.unpaired == 0 for d in paired.differences)
    # the real record shape: retrieval, wrong evidence and judged values are all read
    lookup_cell = before["cells"][SET]["fake"]["lookup"]
    assert _difference(paired, "lookup", "wrong_evidence.rate").n == \
        lookup_cell["wrong_evidence"]["n"]
    assert _difference(paired, "lookup", "metrics.recall@5").n == lookup_cell["n"]


def test_a_clear_difference_counts():
    before = _run([_question(f"q{i}", "lookup", recall=0.0) for i in range(12)])
    after = _run([_question(f"q{i}", "lookup", recall=1.0) for i in range(12)])
    d = _difference(compare_paired(before, after), "lookup", "metrics.recall@5")
    assert (d.n, d.mean, d.low, d.high) == (12, 1.0, 1.0, 1.0)
    assert d.counts and not d.directional and d.label is None


def test_a_difference_whose_interval_spans_zero_does_not_count():
    recalls = [0.0, 1.0] * 6
    before = _run([_question(f"q{i}", "lookup", recall=0.5) for i in range(12)])
    after = _run([_question(f"q{i}", "lookup", recall=r) for i, r in enumerate(recalls)])
    d = _difference(compare_paired(before, after), "lookup", "metrics.recall@5")
    assert d.mean == 0 and d.low < 0 < d.high and not d.counts


def test_intervals_are_reproducible_for_a_fixed_seed():
    noisy = [0.0, 0.2, 1.0, 0.5, 0.9, 0.1, 0.7, 0.3, 0.8, 0.4, 0.6]
    before = _run([_question(f"q{i}", "local", recall=0.5) for i in range(len(noisy))])
    after = _run([_question(f"q{i}", "local", recall=r) for i, r in enumerate(noisy)])
    first, second = (_difference(compare_paired(before, after), "local", "metrics.recall@5")
                     for _ in range(2))
    assert (first.low, first.high) == (second.low, second.high)
    assert first.low < first.mean < first.high


def test_a_class_under_ten_shared_questions_is_directional():
    before = _run([_question(f"q{i}", "global", recall=0.0) for i in range(9)])
    after = _run([_question(f"q{i}", "global", recall=1.0) for i in range(9)])
    d = _difference(compare_paired(before, after), "global", "metrics.recall@5")
    assert d.n == 9 and d.directional and d.counts


def test_pooled_pairs_the_answerable_classes_only():
    before = _run([_question("q1", "lookup", recall=0.0, faithfulness=0.0),
                   _question("q2", "local", recall=0.5, faithfulness=0.0),
                   _question("q3", "decline", faithfulness=0.0),
                   _question("q4", "unanswerable", faithfulness=0.0)])
    after = _run([_question("q1", "lookup", recall=1.0, faithfulness=1.0),
                  _question("q2", "local", recall=0.5, faithfulness=1.0),
                  _question("q3", "decline", faithfulness=0.0),
                  _question("q4", "unanswerable", faithfulness=0.0)])
    paired = compare_paired(before, after)
    assert (_difference(paired, POOLED, "metrics.recall@5").n,
            _difference(paired, POOLED, "metrics.recall@5").mean) == (2, 0.5)
    pooled_judged = _difference(paired, POOLED, "judged.metrics.faithfulness.mean")
    assert (pooled_judged.n, pooled_judged.mean) == (2, 1.0)


def test_classes_follow_the_schema_order_then_pooled():
    before = _run([_question("q1", "global", recall=0.0), _question("q2", "lookup", recall=0.0)])
    after = _run([_question("q1", "global", recall=1.0), _question("q2", "lookup", recall=1.0)])
    classes = [d.class_ for d in compare_paired(before, after).differences]
    assert list(dict.fromkeys(classes)) == ["lookup", "global", POOLED]


def test_questions_in_only_one_run_are_listed_and_not_paired():
    before = _run([_question("q1", "lookup", recall=0.0), _question("q2", "lookup", recall=0.0)])
    after = _run([_question("q1", "lookup", recall=1.0), _question("q3", "lookup", recall=1.0)])
    paired = compare_paired(before, after)
    assert paired.only_before == ((SET, "q2"),)
    assert paired.only_after == ((SET, "q3"),)
    assert _difference(paired, "lookup", "metrics.recall@5").n == 1


def test_a_metric_pairs_only_questions_where_both_runs_have_it():
    before = _run([_question("q1", "lookup", wrong=False, faithfulness=0.5),
                   _question("q2", "lookup", wrong=True),
                   _question("q3", "lookup", faithfulness=0.2)])
    after = _run([_question("q1", "lookup", wrong=True, faithfulness=1.0),
                  _question("q2", "lookup", wrong=None),
                  _question("q3", "lookup")])
    paired = compare_paired(before, after)
    wrong = _difference(paired, "lookup", "wrong_evidence.rate")
    assert (wrong.n, wrong.mean, wrong.unpaired) == (1, 1.0, 1)
    faithfulness = _difference(paired, "lookup", "judged.metrics.faithfulness.mean")
    assert (faithfulness.n, faithfulness.mean, faithfulness.unpaired) == (1, 0.5, 1)
    assert faithfulness.label == "uncalibrated"


def test_a_metric_no_question_pairs_on_is_left_out():
    before = _run([_question("q1", "decline", faithfulness=1.0)])
    after = _run([_question("q1", "decline", faithfulness=1.0)])
    metrics = {d.metric for d in compare_paired(before, after).differences}
    assert metrics == {"judged.metrics.faithfulness.mean"}


def test_paired_comparison_refuses_a_different_answer_prompt():
    before, after = _run([]), _run([])
    after["header"]["answer_prompt"] = {"version": "v2", "sha256": "c" * 64}
    with pytest.raises(CompareError, match="answer_prompt"):
        compare_paired(before, after)


def test_paired_comparison_allows_two_arms():
    before = _run([_question("q1", "lookup", recall=0.0)], arm="vector")
    after = _run([_question("q1", "lookup", recall=1.0)], arm="graph_local")
    assert _difference(compare_paired(before, after), "lookup", "metrics.recall@5").mean == 1.0


@pytest.mark.parametrize("questions", [
    "not a list",
    ["not a record"],
    [{"id": "q1", "question_set": SET}],
    [{**_question("q1", "lookup"), "metrics": {"recall@5": "high"}}],
    [{**_question("q1", "lookup"), "metrics": ["recall@5"]}],
    [_question("q1", "lookup"), _question("q1", "lookup")],
])
def test_malformed_question_records_are_refused(questions):
    with pytest.raises(CompareError, match="^malformed question"):
        compare_paired(_run(questions), _run([]))


def test_the_command_prints_paired_differences_and_unpaired_questions(tmp_path, capsys):
    before = _run([_question(f"q{i}", "lookup", recall=0.0, faithfulness=0.5)
                   for i in range(12)] + [_question("q99", "local", recall=0.0)])
    after = _run([_question(f"q{i}", "lookup", recall=1.0, faithfulness=0.5)
                  for i in range(12)])
    paths = []
    for name, document in (("before.json", before), ("after.json", after)):
        paths.append(tmp_path / name)
        paths[-1].write_text(json.dumps(document), encoding="utf-8")
    assert main([str(p) for p in paths]) == 0
    out = capsys.readouterr().out
    (recall_line,) = [line for line in out.splitlines()
                      if line.startswith(f"{SET}\tlookup\tmetrics.recall@5\t12\t")]
    assert recall_line.endswith("\t0\tyes")
    (judged_line,) = [line for line in out.splitlines()
                      if "\tlookup\tjudged.metrics.faithfulness.mean\t" in line]
    assert "uncalibrated" in judged_line and judged_line.endswith("\t0\tno")
    assert "only in before: 1 (agent_drafted:q99)" in out
    assert "only in after: 0" in out
    assert "seed 7" in out
