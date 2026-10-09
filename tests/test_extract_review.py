"""`python -m extract.review`: the researcher's review round of extracted edges (Step 8).

Driven with a scripted reviewer on a fake clock, as eval.review's tests are.
The candidates are built around real chunks of the NVDA FY2026 10-K fixture.
"""
import json
from collections import Counter

import pytest

from extract.pipeline import extract_filing
from extract.prompt import load_prompt
from extract.report import candidate_lines
from extract.report import write_candidates as report_candidates
from extract.review import EXIT_RUN_ERROR, EXIT_USAGE, main, review
from extract.review_round import wilson
from extract.review_sample import load_candidates, quotas
from retrieve.answer_model import ApiResponse
from tests.anthropic_fixtures import FIXTURES
from tests.extract_fakes import NVDA_FILING
from tests.graph_test_data import NVDA_10K
from tests.review_fakes import (
    PROMPT,
    REAL_MIX,
    Reviewer,
    edge_lines,
    judgments,
    node_line,
    round_lines,
    span_of,
    text_chunks,
    write_candidates,
)

ALL_CORRECT = ["c"] * 30 + ["n"] * 5
RECORDED = ("extract_risk_factor", "extract_segment_note", "extract_suppliers",
            "extract_statement_table")


class _Replay:
    def __init__(self, bodies):
        self._bodies = bodies

    def complete(self, request):
        return ApiResponse(body=self._bodies[request.chunk_id], api_ms=1.0, from_cache=True)


@pytest.fixture(scope="module")
def chunks(nvda_chunks):
    return text_chunks(nvda_chunks)


@pytest.fixture
def candidates(tmp_path, chunks):
    return load_candidates(write_candidates(tmp_path / "extract", edge_lines(chunks, REAL_MIX)))


def _review(candidates, chunks, rounds, person, **kwargs):
    return review(candidates, chunks, rounds, reviewer="rohan", commit="abc1234",
                  ask=person.ask, out=person.out, clock=person.clock, **kwargs)


def _shown_edges(rounds, number=1):
    return [j["edge_id"] for j in judgments(round_lines(rounds, number))]


# ── Sampling ──────────────────────────────────────────────────────────────────

def test_quotas_follow_counts_with_at_least_one_per_type():
    got = quotas({"A": 100, "B": 1, "C": 9}, 10)
    assert sum(got.values()) == 10
    assert got["B"] == 1 and got["C"] >= 1 and got["A"] > got["C"]


def test_quotas_never_exceed_the_edges_there_are():
    assert quotas({"A": 2, "B": 1}, 30) == {"A": 2, "B": 1}


def test_a_round_judges_30_edges_with_every_type_present(candidates, chunks, tmp_path):
    summary = _review(candidates, chunks, tmp_path, Reviewer(ALL_CORRECT))
    shown = judgments(round_lines(tmp_path, 1))
    types = Counter(j["triple"]["type"] for j in shown)
    assert len(shown) == 30 and len({j["edge_id"] for j in shown}) == 30
    assert set(types) == set(REAL_MIX)
    assert types["REPORTS"] > types["OWNS"]
    assert summary["correct"] == 30 and summary["judged"] == 30


def test_the_sample_is_fixed_by_the_seed(candidates, chunks, tmp_path):
    for name, seed in (("a", 7), ("b", 7), ("c", 8)):
        _review(candidates, chunks, tmp_path / name, Reviewer(ALL_CORRECT), seed=seed)
    assert _shown_edges(tmp_path / "a") == _shown_edges(tmp_path / "b")
    assert _shown_edges(tmp_path / "a") != _shown_edges(tmp_path / "c")


def test_a_second_round_draws_no_edge_judged_before(candidates, chunks, tmp_path):
    _review(candidates, chunks, tmp_path, Reviewer(ALL_CORRECT))
    _review(candidates, chunks, tmp_path, Reviewer(ALL_CORRECT))
    first, second = _shown_edges(tmp_path, 1), _shown_edges(tmp_path, 2)
    assert len(second) == 30 and not set(first) & set(second)


# ── Judging ───────────────────────────────────────────────────────────────────

def test_each_triple_is_shown_with_its_chunk_and_the_span_highlighted(tmp_path, chunks):
    line = edge_lines(chunks, {"SUPPLIES": 1}, confidences=("implied",))[0]
    chunk_id = line["properties"]["chunk_ids"][0]
    end_key = line["end"]["key"]["key"]
    span = span_of(chunks[chunk_id])
    flag = {"kind": "flag", "chunk_id": chunk_id, "reason": "name_not_in_span",
            "item": f"Organization(key='{end_key}')"}
    path = write_candidates(tmp_path / "x", [line, node_line(end_key, "Acme Foundry", chunk_id,
                                                               span), flag])
    person = Reviewer(["c", "n", "n", "n", "n", "n"])
    _review(load_candidates(path), chunks, tmp_path, person)
    shown = person.text
    for part in ("SUPPLIES", "Company(cik='1045810')", "Acme Foundry", "implied",
                 "name_not_in_span", chunk_id, f">>>{span}<<<"):
        assert part in shown


def test_a_wrong_answer_needs_a_reason(candidates, chunks, tmp_path):
    person = Reviewer(["w", "", "  ", "direction reversed", *ALL_CORRECT[1:]])
    _review(candidates, chunks, tmp_path, person)
    first = judgments(round_lines(tmp_path, 1))[0]
    assert first["verdict"] == "wrong" and first["reason"] == "direction reversed"
    assert sum("reason" in p.lower() for p in person.prompts) == 3


def test_skips_do_not_count_and_are_replaced(candidates, chunks, tmp_path):
    summary = _review(candidates, chunks, tmp_path, Reviewer(["s", "s", *ALL_CORRECT]))
    lines = judgments(round_lines(tmp_path, 1))
    skipped = [j for j in lines if j["verdict"] == "skip"]
    judged = [j for j in lines if j["verdict"] != "skip"]
    assert len(skipped) == 2 and len(judged) == 30
    assert not {j["edge_id"] for j in skipped} & {j["edge_id"] for j in judged}
    assert summary["judged"] == 30 and summary["skipped"] == 2


def test_a_fast_answer_asks_for_another_look_once(candidates, chunks, tmp_path):
    person = Reviewer(["w", "y", "c", *["c", "n"] * 29, *["n"] * 5], pace=4.0)
    _review(candidates, chunks, tmp_path, person)
    first = judgments(round_lines(tmp_path, 1))[0]
    assert first["verdict"] == "correct" and first["looked_again"] is True
    assert first["seconds"] == pytest.approx(12.0)
    # asked once for the first triple, then once for each of the other 29
    assert sum("another look" in p for p in person.prompts) == 30


def test_a_slow_answer_is_not_questioned(candidates, chunks, tmp_path):
    person = Reviewer(ALL_CORRECT, pace=15.0)
    _review(candidates, chunks, tmp_path, person)
    assert not any("another look" in p for p in person.prompts)


def test_each_judgment_records_its_provenance(candidates, chunks, tmp_path):
    _review(candidates, chunks, tmp_path, Reviewer(ALL_CORRECT))
    first = judgments(round_lines(tmp_path, 1))[0]
    assert first["reviewer"] == "rohan" and first["commit"] == "abc1234"
    assert first["prompt_version"] == PROMPT and first["seconds"] == pytest.approx(20.0)
    assert first["slot_type"] == first["triple"]["type"]
    assert first["triple"]["start"] == {"label": "Company", "key": {"cik": "1045810"}}
    assert first["evidence"]["chunk_id"].startswith(NVDA_10K)
    assert first["candidates_file"] == f"{NVDA_10K}-run1.jsonl"


# ── Quit and resume ───────────────────────────────────────────────────────────

def test_quit_and_resume_lose_and_repeat_nothing(candidates, chunks, tmp_path):
    assert _review(candidates, chunks, tmp_path, Reviewer(["c"] * 5 + ["q"])) is None
    assert len(judgments(round_lines(tmp_path, 1))) == 5
    assert _review(candidates, chunks, tmp_path, Reviewer(["c"] * 12)) is None  # end of input
    summary = _review(candidates, chunks, tmp_path, Reviewer(ALL_CORRECT[17:]))
    resumed = _shown_edges(tmp_path, 1)
    straight = tmp_path / "straight"
    _review(candidates, chunks, straight, Reviewer(ALL_CORRECT))
    assert resumed == _shown_edges(straight) and summary["judged"] == 30
    assert not (tmp_path / f"{NVDA_10K}-round2.jsonl").exists()


def test_a_round_resumes_only_on_its_own_candidates(candidates, chunks, tmp_path):
    _review(candidates, chunks, tmp_path, Reviewer(["c"] * 3))
    other = load_candidates(write_candidates(tmp_path / "x", edge_lines(chunks, {"OWNS": 40}),
                                             run=2))
    with pytest.raises(ValueError, match="run1"):
        _review(other, chunks, tmp_path, Reviewer(["c"]))


# ── Miss check and summary ────────────────────────────────────────────────────

def test_the_miss_check_shows_5_whole_chunks_and_needs_a_note_for_yes(candidates, chunks,
                                                                     tmp_path):
    person = Reviewer(["c"] * 30 + ["y", "", "no supplier edges", "n", "n", "n", "n"])
    summary = _review(candidates, chunks, tmp_path, person)
    checks = [line for line in round_lines(tmp_path, 1) if line["kind"] == "miss_check"]
    assert len(checks) == 5 and len({c["chunk_id"] for c in checks}) == 5
    assert checks[0]["missing"] is True and checks[0]["note"] == "no supplier edges"
    for check in checks:
        assert chunks[check["chunk_id"]] in person.text
    assert summary["miss_check"] == {"chunks": 5, "missing": 1, "rate": 0.2,
                                     "label": "directional"}


def test_wilson_interval_at_26_of_30():
    low, high = wilson(26, 30)
    assert round(low, 2) == 0.70 and round(high, 2) == 0.95


def test_the_summary_reports_accuracy_confidence_levels_and_pace(candidates, chunks, tmp_path):
    answers = ["w", "wrong label"] * 4 + ["c"] * 26 + ["n"] * 5
    summary = _review(candidates, chunks, tmp_path, Reviewer(answers, pace=30.0))
    assert summary["correct"] == 26 and summary["judged"] == 30
    assert summary["accuracy"] == pytest.approx(26 / 30)
    assert [round(x, 2) for x in summary["wilson_95"]] == [0.70, 0.95]
    levels = summary["by_confidence"]
    assert sum(level["judged"] for level in levels.values()) == 30
    assert sum(level["correct"] for level in levels.values()) == 26
    assert summary["median_seconds"] == pytest.approx(30.0)
    assert round_lines(tmp_path, 1)[-1] == {"kind": "summary", **summary}


def test_reviewer_name_is_required(candidates, chunks, tmp_path):
    with pytest.raises(ValueError, match="reviewer"):
        review(candidates, chunks, tmp_path, reviewer=" ", commit="abc1234",
               ask=input, out=print, clock=lambda: 0.0)


# ── Candidates file and command ───────────────────────────────────────────────

def test_a_malformed_candidates_file_is_refused(tmp_path, chunks):
    line = edge_lines(chunks, {"OWNS": 1})[0]
    line["properties"]["confidences"] = []
    with pytest.raises(ValueError, match="line 1"):
        load_candidates(write_candidates(tmp_path, [line]))


def test_review_cli_refuses_non_interactive_input(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["--reviewer", "rohan", "--accession", NVDA_10K]) == EXIT_USAGE
    assert "interactive" in capsys.readouterr().err


def test_a_real_runs_candidates_file_is_read_and_reviewed(tmp_path, nvda_chunks, chunks):
    bodies = {r["chunk_id"]: r["response"] for r in (
        json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        for name in RECORDED)}
    run = extract_filing(NVDA_FILING, [nvda_chunks[c] for c in bodies], _Replay(bodies),
                         load_prompt())
    path = tmp_path / "extract" / f"{NVDA_10K}-run1.jsonl"
    report_candidates(path, candidate_lines(run))
    loaded = load_candidates(path)
    assert len(loaded.edges) == len(run.edges) > 0
    person = Reviewer(["c"] * len(run.edges) + ["n"] * 5)
    summary = _review(loaded, chunks, tmp_path, person)
    assert summary["judged"] == min(30, len(run.edges))
    assert "the span was not found" not in person.text


def test_review_cli_without_a_candidates_file_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    code = main(["--reviewer", "rohan", "--accession", NVDA_10K],
                candidates_dir=tmp_path, rounds_dir=tmp_path)
    assert code == EXIT_RUN_ERROR and "extract.run" in capsys.readouterr().err


def test_review_cli_needs_the_database_url(tmp_path, monkeypatch, chunks, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    write_candidates(tmp_path, edge_lines(chunks, {"OWNS": 1}))
    code = main(["--reviewer", "rohan", "--accession", NVDA_10K],
                candidates_dir=tmp_path, rounds_dir=tmp_path)
    assert code == EXIT_RUN_ERROR and "DATABASE_URL" in capsys.readouterr().err


def test_review_cli_refuses_a_bad_accession(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--reviewer", "rohan", "--accession", "../etc"])
    assert exit_info.value.code == EXIT_USAGE


def test_a_round_file_line_missing_a_field_is_refused(candidates, chunks, tmp_path):
    _review(candidates, chunks, tmp_path, Reviewer(["c"] * 2))
    path = tmp_path / f"{NVDA_10K}-round1.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    broken = json.loads(lines[1])
    del broken["verdict"]
    path.write_text("\n".join([lines[0], json.dumps(broken)]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        _review(candidates, chunks, tmp_path, Reviewer(["c"]))
