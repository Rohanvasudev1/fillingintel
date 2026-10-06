"""The first gate baseline reproduces the committed baseline run (Step 6 ticket 03).

Both files are committed, so this runs in CI with no database.  Recall must
match the run's cells exactly.  The run's wrong-evidence rate also counts cited
hard negatives, so the gate's rate is checked against the run's per-question
hits whose ``above_gold`` is true.

The baseline checked is the file's current version.  When a deliberate,
user-approved update replaces it, this test moves to that version's history.
"""
import json
from statistics import fmean

import pytest

from eval.gate_baseline import BASELINE_PATH, read_baseline
from eval.gate_scores import GATED_CLASSES, POOLED
from ingest.provenance import REPO_ROOT

RUN_PATH = REPO_ROOT / "benchmarks" / "runs" / "2026-10-05-vector-dev-96e8c69.json"
SET_NAME, ARM = "agent_drafted", "vector"


@pytest.fixture(scope="module")
def run():
    return json.loads(RUN_PATH.read_text())


@pytest.fixture(scope="module")
def baseline():
    return read_baseline(BASELINE_PATH)


def _answerable(run, cls=None):
    return [q for q in run["questions"]
            if q["question_set"] == SET_NAME and q["class"] in GATED_CLASSES
            and (cls is None or q["class"] == cls)]


def _above_gold_rate(questions):
    with_negatives = [q for q in questions if q["wrong_evidence"] is not None]
    return fmean(any(h["above_gold"] for h in q["wrong_evidence"]) for q in with_negatives)


def test_the_baseline_was_measured_like_the_run(run, baseline):
    assert baseline.k == run["header"]["k"]
    assert baseline.embedding_model == run["header"]["models"]["embedding"]
    assert baseline.split == run["header"]["split"]
    assert baseline.label == run["header"]["label"]
    assert list(baseline.scores) == [*GATED_CLASSES, POOLED]


@pytest.mark.parametrize("cls", GATED_CLASSES)
def test_recall_per_class_matches_the_run_exactly(run, baseline, cls):
    cell = run["cells"][SET_NAME][ARM][cls]
    scores = baseline.scores[cls]
    assert scores.n == cell["n"]
    assert scores.recall_at_5 == cell["metrics"]["recall@5"]
    assert scores.recall_at_10 == cell["metrics"]["recall@10"]


def test_pooled_recall_matches_the_runs_questions_exactly(run, baseline):
    questions = _answerable(run)
    pooled = baseline.scores[POOLED]
    assert pooled.n == len(questions) == 82
    assert pooled.recall_at_5 == fmean(q["metrics"]["recall@5"] for q in questions)
    assert pooled.recall_at_10 == fmean(q["metrics"]["recall@10"] for q in questions)


@pytest.mark.parametrize("cls", [*GATED_CLASSES, None])
def test_wrong_evidence_matches_the_runs_above_gold_hits(run, baseline, cls):
    scores = baseline.scores[cls or POOLED]
    assert scores.wrong_evidence == _above_gold_rate(_answerable(run, cls))
