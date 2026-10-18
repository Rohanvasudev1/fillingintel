"""Score the drawn dev answers yourself, for judge calibration (Step 13 ticket 04).

Usage (the user only; it refuses piped input)::

    uv run python -m eval.calibrate --reviewer "Rohan Vasudev"
    uv run python -m eval.calibrate --reviewer "Rohan Vasudev" --rescore 10

For each answer in ``benchmarks/calibration/draw.json`` it shows the question,
the kept sentences with their citations and every retrieved chunk with its ID.
For an answerable question's answer you label each claim from
``claims.jsonl`` supported, not supported (with a reason) or not a claim, may
add claims the split missed, answer for each kept sentence whether its cited
chunks support it, and rate relevancy 1 to 5 on the rubric shown.  For a
decline or unanswerable question's answer you say whether it behaved as it
should.  The judge's scores stay hidden until your entry is saved.  An answer
finished in under 15 seconds is offered one more look.  Entries go to
``benchmarks/calibration/scores-{reviewer}.jsonl``; a new sitting starts at the
first answer you have not scored, and ``q`` or Ctrl-D stops without saving the
answer in progress.  ``--rescore N`` shows N drawn answers again, chosen by the
draw's seed, as new entries for intra-rater agreement; your earlier entries are
never shown.
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from eval.calibration.answers import (
    RUNS_DIR,
    AnswerFileError,
    RunAnswer,
    chunk_texts,
    load_answers,
)
from eval.calibration.claims import Claim, ClaimsError, read_claims
from eval.calibration.display import (
    BEHAVIOUR_QUESTIONS,
    CLAIM_GUIDE,
    RUBRIC,
    show_answer,
    show_judge,
)
from eval.calibration.draw import (
    CALIBRATION_DIR,
    CALIBRATION_SEED,
    DRAW_PATH,
    DrawError,
    drawn_answers,
    read_draw,
)
from eval.calibration.scores import (
    CLAIMS_FILE,
    FAST_SECONDS,
    ScoresFileError,
    append_entry,
    read_entries,
    reviewer_name,
    scored_keys,
    scores_path,
)
from ingest.parsed_files import DEFAULT_PARSED_DIR
from ingest.provenance import git_state

EXIT_OK, EXIT_UNREADABLE = 0, 2
Ask = Callable[[str], str]
Out = Callable[..., None]
Clock = Callable[[], float]
T = TypeVar("T")
_CLAIM_CHOICES = {"s": "supported", "n": "not_supported", "o": "not_a_claim"}
_YES_NO = {"y": True, "n": False}


class _Quit(Exception):
    """The reviewer stopped: ``q``, end of input or Ctrl-C."""


def _ask(ask: Ask, prompt: str) -> str:
    try:
        return ask(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        raise _Quit from None


def _choice(ask: Ask, prompt: str, choices: Mapping[str, T]) -> T:
    while True:
        reply = _ask(ask, prompt).lower()
        if reply == "q":
            raise _Quit
        if reply in choices:
            return choices[reply]


def _reason(ask: Ask) -> str:
    while not (reason := _ask(ask, "Reason (which chunk you checked, what is missing): ")):
        pass
    return reason


def _label(ask: Ask, prompt: str, choices: Mapping[str, str]) -> dict[str, str]:
    label = _choice(ask, prompt, choices)
    return {"label": label, **({"reason": _reason(ask)} if label == "not_supported" else {})}


def _claims(answer: RunAnswer, claims: Sequence[Claim], ask: Ask, out: Out) -> list[dict]:
    for line in CLAIM_GUIDE:
        out(line)
    labelled = []
    for i, claim in enumerate(claims, start=1):
        out(f"Claim {i} of {len(claims)} (sentence {claim.sentence_index}): {claim.text}")
        labelled.append({"sentence_index": claim.sentence_index, "text": claim.text,
                         "source": "agent",
                         **_label(ask, "[s]upported, [n]ot supported, n[o]t a claim, [q]uit: ",
                                  _CLAIM_CHOICES)})
    while True:
        reply = _ask(ask, f"Add a claim the split missed? Sentence number 1-{len(answer.kept)}, "
                          "or Enter for none: ")
        if not reply:
            return labelled
        if reply.lower() == "q":
            raise _Quit
        if not reply.isdigit() or not 1 <= int(reply) <= len(answer.kept):
            continue
        text = _ask(ask, "Claim text: ")
        if text:
            labelled.append({"sentence_index": int(reply), "text": text, "source": "added",
                             **_label(ask, "[s]upported, [n]ot supported, [q]uit: ",
                                      {"s": "supported", "n": "not_supported"})})


def _score_answer(answer: RunAnswer, claims: Sequence[Claim], ask: Ask, out: Out
                  ) -> dict[str, object]:
    labelled = _claims(answer, claims, ask, out)
    out("Citation support: for each kept sentence, do the chunks it cites support every "
        "claim in it?")
    support = [_choice(ask, f"Sentence {i}: [y]es, [n]o, [q]uit: ", _YES_NO)
               for i in range(1, len(answer.kept) + 1)]
    for line in RUBRIC:
        out(line)
    relevancy = _choice(ask, "Relevancy 1-5, [q]uit: ", {str(r): r for r in range(1, 6)})
    return {"claims": labelled, "citation_support": support, "relevancy": relevancy,
            "behaviour_correct": None}


def _score_behaviour(answer: RunAnswer, ask: Ask) -> dict[str, object]:
    correct = _choice(ask, BEHAVIOUR_QUESTIONS[answer.class_] + "[y]es, [n]o, [q]uit: ",
                      _YES_NO)
    return {"claims": None, "citation_support": None, "relevancy": None,
            "behaviour_correct": correct}


def _score(answer: RunAnswer, claims: Sequence[Claim], texts: Mapping[str, str],
           number: int, total: int, ask: Ask, out: Out, clock: Clock) -> dict[str, object]:
    start = clock()
    looked_again = False
    while True:
        show_answer(answer, texts, out, number, total)
        fields = (_score_answer(answer, claims, ask, out) if answer.kind == "answer"
                  else _score_behaviour(answer, ask))
        seconds = clock() - start
        if looked_again or seconds >= FAST_SECONDS:
            return {**fields, "seconds": seconds, "looked_again": looked_again}
        again = _ask(ask, f"That took {seconds:.0f} s. Take another look? [y/N]: ")
        if again.lower() != "y":
            return {**fields, "seconds": seconds, "looked_again": False}
        looked_again = True


def rescore_keys(answers: Sequence[RunAnswer], count: int, seed: int) -> set[str]:
    keys = [a.key for a in answers]
    if not 0 < count <= len(keys):
        raise ValueError(f"--rescore takes 1 to {len(keys)} answers, got {count}")
    return set(random.Random(f"rescore|{seed}").sample(keys, count))


def calibrate(answers: Sequence[RunAnswer], claims: Mapping[str, Sequence[Claim]],
              texts: Mapping[str, str], path: Path, reviewer: str, ask: Ask, out: Out,
              clock: Clock, commit: str, rescore: int = 0,
              seed: int = CALIBRATION_SEED) -> int:
    """Score the answers not yet scored, in draw order; returns how many were saved."""
    entries = read_entries(path)
    if rescore:
        if not {a.key for a in answers} <= scored_keys(entries, rescore=False):
            raise ValueError("score every drawn answer before a re-score")
        chosen = rescore_keys(answers, rescore, seed)
        listed = [a for a in answers if a.key in chosen]
    else:
        listed = list(answers)
    done = scored_keys(entries, rescore=bool(rescore))
    saved = 0
    for number, answer in enumerate(listed, start=1):
        if answer.key in done:
            continue
        try:
            fields = _score(answer, claims.get(answer.key, ()), texts, number, len(listed),
                            ask, out, clock)
        except _Quit:
            break
        append_entry(path, {"key": answer.key, "kind": answer.kind, "reviewer": reviewer,
                            "rescore": bool(rescore), "commit": commit,
                            "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
                            **fields})
        saved += 1
        out(f"Saved answer {number} of {len(listed)}.")
        show_judge(answer, out)
    return saved


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score drawn answers for judge calibration.")
    parser.add_argument("--reviewer", required=True, help="your name")
    parser.add_argument("--rescore", type=int, default=0, metavar="N",
                        help="show N drawn answers again for intra-rater agreement")
    parser.add_argument("--draw", type=Path, default=DRAW_PATH)
    parser.add_argument("--claims", type=Path, default=CALIBRATION_DIR / CLAIMS_FILE)
    parser.add_argument("--parsed-dir", type=Path, default=DEFAULT_PARSED_DIR)
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        # Only the user's scores count as hand scores (CONTEXT.md, calibration).
        print("eval.calibrate needs an interactive terminal: a person scores each answer",
              file=sys.stderr)
        return EXIT_UNREADABLE
    try:
        answers = drawn_answers(read_draw(args.draw), load_answers(RUNS_DIR))
        claims = read_claims(args.claims)
        reviewer = reviewer_name(args.reviewer)
        path = scores_path(reviewer)
        saved = calibrate(answers, claims, chunk_texts(answers, args.parsed_dir), path,
                          reviewer, input, print, time.monotonic, git_state(), args.rescore)
    except (OSError, ValueError, AnswerFileError, DrawError, ClaimsError,
            ScoresFileError) as exc:
        print(f"cannot calibrate: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE
    print(f"{saved} answers saved to {path}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
