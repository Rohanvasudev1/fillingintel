"""``python -m eval.calibrate``: the user scores drawn answers (Step 13 ticket 04).

Real dev answers from the committed runs, cut to one or two sentences to keep the
scripts short, with one claim per sentence as the claims input; the reviewer is
the scripted fake from ``tests/review_fakes.py`` with its fake clock.
"""
from dataclasses import replace

import pytest

from eval import calibrate as calibrate_module
from eval.calibrate import calibrate
from eval.calibration.answers import RUNS_DIR, load_answers
from eval.calibration.claims import Claim
from eval.calibration.scores import read_entries
from tests.review_fakes import Reviewer


@pytest.fixture(scope="module")
def answers():
    pool = load_answers(RUNS_DIR)
    two = [replace(a, kept=a.kept[:2]) for a in pool if a.kind == "answer" and len(a.kept) >= 2]
    decline = next(a for a in pool if a.class_ == "decline")
    return [two[0], two[1], decline]


def _claims(answers):
    return {a.key: tuple(Claim(i, f"claim from sentence {i}")
                         for i in range(1, len(a.kept) + 1)) for a in answers}


def _run(tmp_path, answers, reviewer, rescore=0):
    return calibrate(answers, _claims(answers), texts={}, path=tmp_path / "scores-me.jsonl",
                     reviewer="me", ask=reviewer.ask, out=reviewer.out, clock=reviewer.clock,
                     commit="abc", rescore=rescore)


# One answer of two sentences: two claim labels, no missed claim, two citation verdicts,
# then relevancy.
ANSWER = ["s", "s", "", "y", "y", "5"]
DECLINE = ["y"]


def test_refuses_non_interactive_input(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert calibrate_module.main(["--reviewer", "me"]) == 2
    assert "interactive terminal" in capsys.readouterr().err


def test_scores_are_saved_and_judge_scores_shown_only_after(tmp_path, answers):
    reviewer = Reviewer([*ANSWER, *ANSWER, *DECLINE])
    assert _run(tmp_path, answers, reviewer) == 3
    entries = read_entries(tmp_path / "scores-me.jsonl")
    first = entries[0]
    assert first["key"] == answers[0].key and first["rescore"] is False
    assert [c["label"] for c in first["claims"]] == ["supported", "supported"]
    assert first["citation_support"] == [True, True] and first["relevancy"] == 5
    assert entries[2]["behaviour_correct"] is True
    text = reviewer.text
    assert text.index("Saved") < text.index("Judge (3 runs)"), "judge scores after the save"
    assert text.count("Judge (3 runs)") == 3


def test_resumes_at_the_first_unscored_answer(tmp_path, answers):
    first = Reviewer(ANSWER)  # scores answer 1, then end of input quits
    assert _run(tmp_path, answers, first) == 1
    second = Reviewer([*ANSWER, *DECLINE])
    assert _run(tmp_path, answers, second) == 2
    assert "Answer 2 of 3" in second.text and "Answer 1 of 3" not in second.text
    assert [e["key"] for e in read_entries(tmp_path / "scores-me.jsonl")] == \
        [a.key for a in answers]


def test_quitting_mid_answer_saves_nothing_for_it(tmp_path, answers):
    reviewer = Reviewer(["s", "q"])
    assert _run(tmp_path, answers, reviewer) == 0
    assert not (tmp_path / "scores-me.jsonl").exists()


def test_not_supported_needs_a_reason(tmp_path, answers):
    reviewer = Reviewer(["n", "", "  ", "chunk 0067 gives 21%", "s", "", "y", "n", "4"])
    _run(tmp_path, answers[:1], reviewer)
    (entry,) = read_entries(tmp_path / "scores-me.jsonl")
    assert entry["claims"][0] == {"sentence_index": 1, "text": "claim from sentence 1",
                                  "source": "agent", "label": "not_supported",
                                  "reason": "chunk 0067 gives 21%"}
    assert sum("Reason" in p for p in reviewer.prompts) == 3


def test_a_missed_claim_can_be_added(tmp_path, answers):
    reviewer = Reviewer(["s", "o", "2", "an extra fact", "n", "absent", "", "y", "y", "3"])
    _run(tmp_path, answers[:1], reviewer)
    (entry,) = read_entries(tmp_path / "scores-me.jsonl")
    assert entry["claims"][1]["label"] == "not_a_claim"
    assert entry["claims"][2] == {"sentence_index": 2, "text": "an extra fact",
                                  "source": "added", "label": "not_supported",
                                  "reason": "absent"}


def test_a_fast_answer_is_asked_to_take_another_look(tmp_path, answers):
    reviewer = Reviewer([*ANSWER, "y", *ANSWER], pace=1.0)  # 6 prompts, 6 s
    _run(tmp_path, answers[:1], reviewer)
    assert any("Take another look" in p for p in reviewer.prompts)
    (entry,) = read_entries(tmp_path / "scores-me.jsonl")
    assert entry["looked_again"] is True
    assert sum("Take another look" in p for p in reviewer.prompts) == 1, "asked once"


def test_a_paced_answer_is_not_asked_again(tmp_path, answers):
    reviewer = Reviewer(ANSWER, pace=5.0)
    _run(tmp_path, answers[:1], reviewer)
    assert not any("Take another look" in p for p in reviewer.prompts)
    (entry,) = read_entries(tmp_path / "scores-me.jsonl")
    assert entry["seconds"] == pytest.approx(30.0)


def test_rescore_shows_drawn_answers_again_as_new_entries(tmp_path, answers):
    answers = answers[:2]
    _run(tmp_path, answers, Reviewer([*ANSWER, *ANSWER]))
    reviewer = Reviewer([*ANSWER, *ANSWER])
    assert _run(tmp_path, answers, reviewer, rescore=2) == 2
    entries = read_entries(tmp_path / "scores-me.jsonl")
    rescored = [e for e in entries if e["rescore"]]
    assert len(rescored) == 2
    assert "earlier" not in reviewer.text.lower(), "earlier entries stay hidden"
    assert _run(tmp_path, answers, Reviewer([]), rescore=2) == 0, "resumes: nothing left"


def test_rescore_needs_the_first_pass_done(tmp_path, answers):
    with pytest.raises(ValueError, match="score every drawn answer"):
        _run(tmp_path, answers, Reviewer([]), rescore=2)
