"""Review agent-drafted eval questions; accepted ones become human-verified (Step 4).

Usage::

    uv run python -m eval.review --reviewer "Rohan Vasudev"

Shows each undecided draft from ``eval/agent_drafted_set.jsonl`` with the full
text of its gold chunks.  Accept copies it into ``eval/eval_set.jsonl`` as
``human_verified`` with you as author; reject drops it.  Decisions go to
``eval/review_log.jsonl``, keyed by each draft's question and gold chunks,
so a draft is never shown twice even if the drafts are re-merged.  The
drafts file is never edited.  To change a question or answer, accept it and
then edit ``eval/eval_set.jsonl`` by hand: it is yours.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from eval.corpus_index import ChunkIndex
from eval.review_log import ACCEPT, REJECT, append_decision, read_decisions
from eval.schema import EvalRecord, draft_key, dump_records, load_records, normalise_question
from eval.validate import validate
from ingest.parsed_files import DEFAULT_PARSED_DIR
from ingest.provenance import REPO_ROOT

EVAL_DIR = REPO_ROOT / "eval"
DEFAULT_DRAFTS = EVAL_DIR / "agent_drafted_set.jsonl"
DEFAULT_HUMAN = EVAL_DIR / "eval_set.jsonl"
DEFAULT_LOG = EVAL_DIR / "review_log.jsonl"
EXIT_UNREADABLE = 2
SKIP = "skip"
QUIT = "quit"
_CHOICES = {"a": ACCEPT, "r": REJECT, "s": SKIP, "q": QUIT}

Ask = Callable[[str], str]
Out = Callable[..., None]


def _show(draft: EvalRecord, index: ChunkIndex, out: Out) -> None:
    out(f"Draft {draft.id} [{draft.class_}, {draft.difficulty}] {', '.join(draft.tickers)}")
    out(f"Q: {draft.question}")
    out(f"A: {draft.gold_answer}")
    for chunk_id in draft.gold_chunk_ids:
        out(f"--- gold {chunk_id} ---")
        out(index.text(chunk_id) or "(does not resolve)")
    for neg in draft.hard_negatives:
        out(f"--- hard negative {neg.chunk_id} ({neg.relation}) ---")
    if draft.notes:
        out(f"Notes: {draft.notes}")


def _ask_choice(ask: Ask) -> str:
    """The reviewer's choice; end of input or Ctrl-C means quit."""
    while True:
        try:
            reply = ask("[a]ccept, [r]eject, [s]kip, [q]uit: ").strip().lower()[:1]
        except (EOFError, KeyboardInterrupt):
            return QUIT
        if reply in _CHOICES:
            return _CHOICES[reply]


def _next_id(records: list[EvalRecord]) -> str:
    highest = max((int(r.id[1:]) for r in records), default=0)
    return f"q{highest + 1:04d}"


def _accept(
    draft: EvalRecord, human: list[EvalRecord], index: ChunkIndex, reviewer: str
) -> tuple[EvalRecord | None, list[str]]:
    """The verified copy of *draft*, or None and the reasons it cannot join *human*."""
    record = draft.model_copy(
        update={
            "id": _next_id(human),
            "provenance": "human_verified",
            "author": reviewer,
            "derived_from": draft_key(draft),
        }
    )
    problems = validate([record], index, human_set=True)
    question = normalise_question(record.question)
    if any(normalise_question(r.question) == question for r in human):
        problems.append(f"{draft.id}: the human set already has this question")
    return (None, problems) if problems else (record, [])


def review(
    drafts: Path, human: Path, log: Path, index: ChunkIndex, reviewer: str, ask: Ask, out: Out
) -> int:
    """Walk undecided drafts; returns how many were accepted."""
    reviewer = reviewer.strip()
    if not reviewer:
        raise ValueError("reviewer name is required (it becomes the verified author)")
    pending = load_records(drafts)
    if any(d.provenance != "agent_drafted" for d in pending):
        raise ValueError(f"{drafts} holds records that are not agent_drafted")
    human_records = load_records(human) if human.exists() else []
    decided = set(read_decisions(log)) | {r.derived_from for r in human_records if r.derived_from}
    accepted = 0
    for draft in pending:
        key = draft_key(draft)
        if key in decided:
            continue
        _show(draft, index, out)
        choice = _ask_choice(ask)
        if choice == QUIT:
            break
        if choice == SKIP:
            continue
        if choice == ACCEPT:
            record, problems = _accept(draft, human_records, index, reviewer)
            if record is None:
                out("Not accepted; fix the draft first:", *problems)
                continue
            human_records = [*human_records, record]
            dump_records(human_records, human)  # before the log: a crash re-reads derived_from
            accepted += 1
        append_decision(log, key, draft.id, choice, reviewer)
    return accepted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review agent-drafted eval questions.")
    parser.add_argument("--reviewer", required=True, help="your name; becomes the author")
    parser.add_argument("--drafts", type=Path, default=DEFAULT_DRAFTS)
    parser.add_argument("--human", type=Path, default=DEFAULT_HUMAN)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--parsed-dir", type=Path, default=DEFAULT_PARSED_DIR)
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        # Piped answers would let a script mint human_verified records (CLAUDE.md invariant 1).
        print("eval.review needs an interactive terminal: a person decides each draft",
              file=sys.stderr)
        return EXIT_UNREADABLE
    try:
        index = ChunkIndex.from_parsed_dir(args.parsed_dir)
        accepted = review(args.drafts, args.human, args.log, index, args.reviewer, input, print)
    except (OSError, ValueError) as exc:
        print(f"cannot review: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE
    print(f"{accepted} accepted into {args.human}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
