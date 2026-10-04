"""Tests for ``python -m eval.review``: a human turns agent drafts into verified records."""
import json

import pytest

from eval.corpus_index import ChunkIndex
from eval.review import review
from eval.schema import draft_key, dump_records, load_records
from tests.eval_helpers import make_record


@pytest.fixture(scope="module")
def index(fixture_records):
    return ChunkIndex.from_records(fixture_records)


QUESTIONS = (
    "What was the company's total revenue for the fiscal year?",
    "How did gross margin change year over year?",
    "What does the company say about export controls?",
)


def _drafts(index):
    return [
        make_record(
            index, id=f"q000{i + 1}", question=q, provenance="agent_drafted",
            author="claude-subagent",
        )
        for i, q in enumerate(QUESTIONS)
    ]


@pytest.fixture
def files(tmp_path, index):
    drafted = tmp_path / "agent_drafted_set.jsonl"
    dump_records(_drafts(index), drafted)
    return drafted, tmp_path / "eval_set.jsonl", tmp_path / "review_log.jsonl"


def _answers(*replies):
    it = iter(replies)

    def ask(_prompt):
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return ask


def _run(files, index, *replies, out=lambda *_: None):
    drafted, human, log = files
    return review(drafted, human, log, index, reviewer="rohan", ask=_answers(*replies), out=out)


def _shown_questions(files, index, *replies):
    """Questions of the drafts shown, in order."""
    shown: list[str] = []

    def capture(*args):
        line = " ".join(map(str, args))
        if line.startswith("Q: "):
            shown.append(line[3:])

    _run(files, index, *replies, out=capture)
    return shown


def test_accept_moves_a_verified_copy_to_the_human_set(files, index):
    drafted, human, log = files
    _run(files, index, "a", "r", "q")
    (verified,) = load_records(human)
    first, second = load_records(drafted)[:2]
    assert verified.provenance == "human_verified"
    assert verified.author == "rohan"
    assert verified.derived_from == draft_key(first)
    assert verified.question == first.question
    assert first.provenance == "agent_drafted", "the drafts file is never edited"
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(e["draft_key"], e["decision"]) for e in entries] == [
        (draft_key(first), "accept"),
        (draft_key(second), "reject"),
    ]


def test_decisions_are_logged_and_not_asked_again(files, index):
    _run(files, index, "a", "r", "q")
    assert _shown_questions(files, index, "s", "q") == [QUESTIONS[2]]


def test_skip_is_not_logged(files, index):
    _run(files, index, "s", "q")
    assert _shown_questions(files, index, "q") == [QUESTIONS[0]]


def test_decisions_survive_renumbering_of_the_drafts(files, index):
    drafted, _, _ = files
    _run(files, index, "a", "q")
    drafts = _drafts(index)
    renumbered = [d.model_copy(update={"id": f"q{9 - i:04d}"}) for i, d in enumerate(drafts)]
    dump_records(list(reversed(renumbered)), drafted)
    shown = _shown_questions(files, index, "s", "s", "q")
    assert QUESTIONS[0] not in shown and len(shown) == 2


def test_accepted_draft_missing_from_the_log_is_not_shown_again(files, index):
    """A crash between writing the human set and the log must not re-show the draft."""
    _, _, log = files
    _run(files, index, "a", "q")
    log.unlink()
    assert QUESTIONS[0] not in _shown_questions(files, index, "s", "s", "q")


def test_truncated_log_line_is_skipped(files, index):
    _, _, log = files
    _run(files, index, "r", "q")
    log.write_text(log.read_text() + '{"draft_key": "abc", "deci')
    assert _shown_questions(files, index, "q") == [QUESTIONS[1]]


def test_end_of_input_stops_cleanly(files, index):
    assert _run(files, index, "a") == 1


def test_reviewer_name_is_required(files, index):
    drafted, human, log = files
    with pytest.raises(ValueError, match="reviewer"):
        review(drafted, human, log, index, reviewer=" ", ask=_answers("q"), out=lambda *_: None)


def test_new_ids_follow_the_highest_existing_id(files, index):
    _, human, _ = files
    existing = [
        make_record(index, id="q0001", question="Human one?"),
        make_record(index, id="q0007", question="Human two?"),
    ]
    dump_records(existing, human)
    _run(files, index, "a", "q")
    assert [r.id for r in load_records(human)] == ["q0001", "q0007", "q0008"]


def test_drafts_that_are_not_agent_drafted_are_refused(tmp_path, index):
    drafted = tmp_path / "drafts.jsonl"
    dump_records([make_record(index, question="Human written?")], drafted)
    with pytest.raises(ValueError, match="agent_drafted"):
        review(
            drafted, tmp_path / "eval_set.jsonl", tmp_path / "log.jsonl", index,
            reviewer="rohan", ask=_answers("a"), out=lambda *_: None,
        )


def test_review_cli_without_a_corpus_fails_cleanly(tmp_path, monkeypatch, capsys):
    from eval.review import main

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    args = ["--reviewer", "rohan", "--parsed-dir", str(tmp_path / "absent")]
    assert main(args) == 2
    assert "cannot review" in capsys.readouterr().err


def test_refused_accept_is_reported_and_not_logged(files, index):
    drafted, human, log = files
    dump_records([make_record(index, id="q0001", question=QUESTIONS[0])], human)
    lines: list[str] = []
    _run(files, index, "a", "q", out=lambda *args: lines.extend(map(str, args)))
    assert any("already has this question" in line for line in lines)
    assert not log.exists()


def test_review_cli_refuses_non_interactive_input(tmp_path, monkeypatch, capsys):
    """Piping answers into the review would let a script mint human_verified records."""
    from eval.review import main

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["--reviewer", "rohan", "--parsed-dir", str(tmp_path)]) == 2
    assert "interactive" in capsys.readouterr().err
