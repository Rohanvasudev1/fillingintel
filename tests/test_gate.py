"""``python -m eval.gate``: the offline retrieval quality gate (Step 6 ticket 03, ADR-0003).

Most tests share one throwaway schema loaded once from the committed snapshot,
handed to the gate in place of its own load.  The end-to-end tests let the gate
load the snapshot itself.  Every test runs with the network blocked
(pytest-socket allows localhost only) and with API keys removed.
"""
import json
import os
from collections.abc import MutableMapping
from contextlib import nullcontext

import pytest

from eval.gate import EXIT_DROPPED, EXIT_RUN_ERROR, EXIT_USAGE, main
from eval.gate_baseline import BASELINE_PATH, read_baseline
from eval.gate_scores import compare
from eval.snapshot import SNAPSHOT_PATH, load_snapshot
from tests.conftest import throwaway_schema

API_KEYS = ("VOYAGE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")


@pytest.fixture(scope="module")
def loaded():
    with throwaway_schema() as conn:
        load_snapshot(conn, SNAPSHOT_PATH)
        yield conn


@pytest.fixture(autouse=True)
def no_keys_or_summary(monkeypatch):
    for name in (*API_KEYS, "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def baseline_path(tmp_path):
    return tmp_path / "gate_baseline.json"


@pytest.fixture
def gate(loaded, baseline_path, capsys):
    """Run the gate on the shared loaded schema; returns (exit code, stdout + stderr)."""
    def run(*args, **kwargs):
        kwargs.setdefault("baseline_path", baseline_path)
        kwargs.setdefault("open_database", lambda path: nullcontext(loaded))
        code = main(list(args), **kwargs)
        out = capsys.readouterr()
        return code, out.out + out.err
    return run


def _edit(path, change):
    doc = json.loads(path.read_text())
    change(doc)
    path.write_text(json.dumps(doc))


def _no_database(path):
    raise AssertionError("the gate opened the database before checking its inputs")


# ── pass, drop, determinism ──────────────────────────────────────────────────

def test_the_gate_passes_against_its_own_baseline(gate, baseline_path):
    assert gate("--update-baseline")[0] == 0
    code, out = gate()
    assert code == 0, out
    assert "agent-drafted questions" in out
    for cls in ("lookup", "local", "multi_hop", "global", "pooled"):
        assert cls in out
    assert "DROPPED" not in out


def test_the_gate_passes_against_the_committed_baseline(gate):
    code, out = gate(baseline_path=BASELINE_PATH)
    assert code == 0, out


def test_two_runs_give_identical_numbers(gate, baseline_path):
    gate("--update-baseline")
    first = read_baseline(baseline_path).scores
    gate("--update-baseline")
    assert read_baseline(baseline_path).scores == first
    assert gate()[1] == gate()[1]


def test_a_recall_raised_by_one_questions_worth_fails_naming_the_class(gate, baseline_path):
    gate("--update-baseline")
    scores = read_baseline(baseline_path).scores["multi_hop"]
    raised = scores.recall_at_10 + 1 / scores.n
    _edit(baseline_path, lambda d: d["scores"]["multi_hop"].update({"recall@10": raised}))
    code, out = gate()
    assert code == EXIT_DROPPED
    drops = [line for line in out.splitlines() if line.startswith("DROPPED")]
    assert len(drops) == 1
    assert "multi_hop recall@10" in drops[0]
    assert f"baseline {raised:.6f}" in drops[0]
    assert f"current {scores.recall_at_10:.6f}" in drops[0]
    assert f"delta {-1 / scores.n:+.6f}" in drops[0]


def test_a_wrong_evidence_rate_lowered_by_one_questions_worth_fails(gate, baseline_path):
    gate("--update-baseline")
    scores = read_baseline(baseline_path).scores["global"]
    assert scores.wrong_evidence and scores.wrong_evidence >= 1 / scores.n
    lowered = scores.wrong_evidence - 1 / scores.n
    _edit(baseline_path, lambda d: d["scores"]["global"].update({"wrong_evidence": lowered}))
    code, out = gate()
    assert code == EXIT_DROPPED
    assert "DROPPED global wrong_evidence" in out


def test_k_1_fails_the_gate_and_scores_lower(gate, baseline_path, tmp_path):
    gate("--update-baseline")
    code, out = gate(k=1)
    assert code == EXIT_USAGE
    assert "k is 1" in out
    k1 = tmp_path / "k1.json"
    assert gate("--update-baseline", k=1, baseline_path=k1)[0] == 0
    drops = compare(read_baseline(baseline_path).scores, read_baseline(k1).scores)
    dropped = {(d.class_, d.metric) for d in drops}
    assert {("pooled", "recall@5"), ("pooled", "recall@10")} <= dropped


# ── inputs ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field, value", [
    ("snapshot_sha256", "0" * 64),
    ("question_set_sha256", "0" * 64),
    ("embedding_model", "voyage-4"),
    ("k", 5),
])
def test_an_input_mismatch_is_bad_input_and_runs_nothing(gate, baseline_path, field, value):
    gate("--update-baseline")
    _edit(baseline_path, lambda d: d.update({field: value}))
    code, out = gate(open_database=_no_database)
    assert code == EXIT_USAGE
    assert field in out


def test_a_missing_baseline_is_bad_input(gate):
    code, out = gate(open_database=_no_database)
    assert code == EXIT_USAGE
    assert "--update-baseline" in out


def test_a_damaged_snapshot_means_the_gate_could_not_run(gate, tmp_path):
    bad = tmp_path / "snapshot.jsonl.gz"
    bad.write_bytes(b"not gzip")
    code, _ = gate("--update-baseline", snapshot_path=bad, open_database=_no_database)
    assert code == EXIT_RUN_ERROR


def test_the_gate_has_no_way_to_select_the_test_split(gate):
    with pytest.raises(SystemExit) as exc:
        gate("--split", "test")
    assert exc.value.code == EXIT_USAGE


def test_without_database_url_the_gate_reports_bad_input(monkeypatch, baseline_path, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["--update-baseline"], baseline_path=baseline_path) == EXIT_USAGE
    assert "DATABASE_URL" in capsys.readouterr().err


# ── output ───────────────────────────────────────────────────────────────────

def test_the_job_summary_is_written_only_when_its_variable_is_set(
    gate, baseline_path, tmp_path, monkeypatch
):
    gate("--update-baseline")
    summary = tmp_path / "summary.md"
    gate()
    assert not summary.exists()
    summary.write_text("earlier step\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code, out = gate()
    text = summary.read_text()
    assert code == 0
    assert text.startswith("earlier step\n")
    assert "agent-drafted questions" in text and "| multi_hop |" in text


def test_update_baseline_prints_what_it_changes(gate, baseline_path):
    code, out = gate("--update-baseline")
    assert code == 0 and "no previous gate baseline" in out
    raised = read_baseline(baseline_path).scores["lookup"].recall_at_5 + 0.01
    _edit(baseline_path, lambda d: d["scores"]["lookup"].update({"recall@5": raised}))
    code, out = gate("--update-baseline")
    assert code == 0
    assert "lookup recall@5" in out and "LOWERED" in out
    assert gate()[0] == 0


def test_the_baseline_records_what_it_was_measured_against(gate, baseline_path):
    gate("--update-baseline")
    baseline = read_baseline(baseline_path)
    assert baseline.k == 10
    assert baseline.embedding_model == "voyage-4-large"
    assert baseline.label == "agent-drafted questions"
    assert list(baseline.scores) == ["lookup", "local", "multi_hop", "global", "pooled"]
    assert baseline.scores["pooled"].n == 82


# ── end to end: no API client, no key, no network ───────────────────────────

class _RecordingEnviron(MutableMapping):
    """``os.environ`` stand-in that records every name read."""

    def __init__(self, data):
        self._data, self.read = dict(data), set()

    def __getitem__(self, key):
        self.read.add(key)
        return self._data[key]

    def get(self, key, default=None):
        self.read.add(key)
        return self._data.get(key, default)

    def __contains__(self, key):
        self.read.add(key)
        return key in self._data

    def __setitem__(self, key, value):
        self._data[key] = value

    def __delitem__(self, key):
        del self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


def _refuse(*args, **kwargs):
    raise AssertionError("the gate constructed an API client")


def test_the_gate_loads_the_snapshot_itself_with_no_api_client_or_key(
    gate, baseline_path, monkeypatch, capsys
):
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL is not set")
    import openai

    from ingest.voyage import VoyageClient
    from retrieve.answer_model import AnthropicAnswerModel

    gate("--update-baseline")
    shared = read_baseline(baseline_path).scores
    for cls in (VoyageClient, AnthropicAnswerModel, openai.OpenAI):
        monkeypatch.setattr(cls, "__init__", _refuse)
    environ = _RecordingEnviron(os.environ)
    monkeypatch.setattr(os, "environ", environ)
    assert main([], baseline_path=baseline_path) == 0, capsys.readouterr()
    assert not environ.read & set(API_KEYS)
    own = baseline_path.with_name("own.json")
    assert main(["--update-baseline"], baseline_path=own) == 0
    assert read_baseline(own).scores == shared


def test_update_refuses_to_replace_an_invalid_baseline(gate, baseline_path):
    baseline_path.write_text('{"k": 10}')
    code, out = gate("--update-baseline", open_database=_no_database)
    assert code == EXIT_USAGE
    assert "gate baseline" in out
    assert baseline_path.read_text() == '{"k": 10}'


def test_an_unwritable_job_summary_means_the_gate_could_not_run(
    gate, baseline_path, tmp_path, monkeypatch
):
    gate("--update-baseline")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path))  # a directory, not a file
    code, out = gate()
    assert code == EXIT_RUN_ERROR
    assert "GITHUB_STEP_SUMMARY" in out


def test_a_baseline_missing_a_class_is_bad_input(gate, baseline_path):
    gate("--update-baseline")
    _edit(baseline_path, lambda d: d["scores"].pop("global"))
    code, out = gate()
    assert code == EXIT_USAGE
    assert "classes" in out
