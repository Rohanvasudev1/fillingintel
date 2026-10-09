"""Judge a run's extracted edges, one review round at a time (Step 8). The user runs this.

Usage (needs ``DATABASE_URL`` for the chunk texts; no network)::

    uv run --env-file .env python -m extract.review --reviewer "<name>" --accession <no>
    uv run --env-file .env python -m extract.review --reviewer "<name>" --accession <no> --run 2

A round draws 30 edges from the run's candidates file (``data/extract/``, the
latest run unless ``--run`` is given), seeded and stratified by edge type
(extract.review_sample), leaving out every edge judged in an earlier round of
the filing. Each is shown with its chunk and the evidence span marked; answer
correct, wrong (with a one-line reason) or skip. Correct means the right
labels, direction and properties and a span that states it; partly right is
wrong. A skip does not count, and another edge is drawn. Then 5 whole chunks
are shown with every triple read from them, for a miss check.

Answers are written as they are given to
``benchmarks/extraction/{accession_no}-round{N}.jsonl`` (committed). Quit with
q or Ctrl-D and run the same command to resume the open round. The round ends
with a summary line: accuracy with a Wilson 95% interval, accuracy per
confidence level, the median time per answer and the miss rate (directional).

Only a person's answers count as extraction accuracy, so the command refuses
input that is not a terminal. Exit codes: 0 round finished or saved for later;
2 bad arguments or not a terminal; 3 could not run.
"""
from __future__ import annotations

import argparse
import os
import random
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from extract.report import CANDIDATES_DIR, REPORTS_DIR
from extract.review_display import Out, show_chunk, show_summary, show_triple
from extract.review_round import (
    CORRECT,
    FAST_SECONDS,
    JUDGMENT,
    MISS_CHECK,
    SKIP,
    SUMMARY,
    WRONG,
    Round,
    append_line,
    numbered_files,
    read_rounds,
    round_path,
    summarize,
)
from extract.review_sample import SEED, Candidates, ReviewEdge, Sampler, load_candidates
from ingest.provenance import git_state
from ingest.store import filing_chunk_texts

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3
ROUND_SIZE = 30
MISS_CHUNKS = 5
DATABASE_ENV = "DATABASE_URL"
_VERDICTS = {"c": CORRECT, "w": WRONG, "s": SKIP}
_QUIT = "q"
_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")

Ask = Callable[[str], str]
Clock = Callable[[], float]
Connect = Callable[[str], AbstractContextManager[psycopg.Connection]]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class _Quit(Exception):
    """The reviewer quit; everything answered so far is already written."""


def _ask(ask: Ask, prompt: str) -> str:
    try:
        return ask(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        raise _Quit from None


def _ask_text(ask: Ask, prompt: str) -> str:
    """A non-empty line; an empty answer asks again."""
    while not (reply := _ask(ask, prompt)):
        pass
    return reply


def _ask_choice(ask: Ask, prompt: str, choices: Mapping[str, str]) -> str:
    while True:
        reply = _ask(ask, prompt).lower()[:1]
        if reply == _QUIT:
            raise _Quit
        if reply in choices:
            return choices[reply]


@dataclass(frozen=True)
class _Session:
    """One sitting of one round: what every line records, and the injected I/O."""

    candidates: Candidates
    chunks: Mapping[str, str]
    path: Path
    number: int
    reviewer: str
    commit: str
    seed: int
    ask: Ask
    out: Out
    clock: Clock

    def line(self, kind: str, **fields: object) -> dict[str, object]:
        return {"kind": kind, "round": self.number, "reviewer": self.reviewer,
                "commit": self.commit, "candidates_file": self.candidates.file_name,
                "candidates_sha256": self.candidates.sha256, "seed": self.seed,
                "at": _now(), **fields}


def review(
    candidates: Candidates, chunks: Mapping[str, str], rounds_dir: Path, *, reviewer: str,
    commit: str, ask: Ask, out: Out, clock: Clock, seed: int = SEED, size: int = ROUND_SIZE,
    miss_chunks: int = MISS_CHUNKS,
) -> dict[str, object] | None:
    """Run or resume the open round; its summary when finished, None when the reviewer quit."""
    name = reviewer.strip()
    if not name:
        raise ValueError("reviewer name is required (it is recorded on every judgment)")
    _check_chunks(candidates, chunks)
    rounds = read_rounds(rounds_dir, candidates.accession_no)
    current = rounds[-1] if rounds and not rounds[-1].closed else None
    number = current.number if current else max((r.number for r in rounds), default=0) + 1
    if current is not None:
        _check_resume(current, candidates)
    excluded = {e for r in rounds if r is not current for e in r.judged_edges()}
    sampler = Sampler.build(candidates.edges, excluded, size, seed, number)
    path = round_path(rounds_dir, candidates.accession_no, number)
    session = _Session(candidates, chunks, path, number, name, commit, seed, ask, out, clock)
    lines = current.lines if current else ()
    try:
        _judge_edges(session, sampler, [line for line in lines if line["kind"] == JUDGMENT])
        _miss_check(session, sampler, miss_chunks,
                    {str(line["chunk_id"]) for line in lines if line["kind"] == MISS_CHECK})
    except _Quit:
        out(f"Saved to {path.name}; run the same command to resume round {number}.")
        return None
    finished = read_rounds(rounds_dir, candidates.accession_no)[-1]
    summary = summarize(finished, size=len(sampler.slots), commit=commit,
                        closed_at=_now())
    append_line(path, {"kind": SUMMARY, **summary})
    return summary


def _check_chunks(candidates: Candidates, chunks: Mapping[str, str]) -> None:
    unknown = {ev.chunk_id for e in candidates.edges for ev in e.evidence} - set(chunks)
    if unknown:
        raise ValueError(f"{len(unknown)} evidence chunks of {candidates.file_name} have no "
                         f"text, e.g. {min(unknown)}")


def _check_resume(current: Round, candidates: Candidates) -> None:
    started_on = {(str(line["candidates_file"]), str(line["candidates_sha256"]))
                  for line in current.lines}
    if started_on and started_on != {(candidates.file_name, candidates.sha256)}:
        names = ", ".join(sorted(name for name, _ in started_on))
        raise ValueError(f"round {current.number} is open on {names}; finish it with that run "
                         f"(--run), or check that file is unchanged")


def _judge_edges(session: _Session, sampler: Sampler,
                 recorded: Sequence[Mapping[str, object]]) -> None:
    """Fill every slot with a verdict, replaying what the open round already holds."""
    verdicts = {str(j["edge_id"]): str(j["verdict"]) for j in recorded}
    used: set[str] = set()
    judged = sum(1 for v in verdicts.values() if v != SKIP)
    for slot in range(len(sampler.slots)):
        while (edge := sampler.draw(slot, used)) is not None:
            used.add(edge.edge_id)
            verdict = verdicts.get(edge.edge_id)
            if verdict is None:
                position = f"[{judged + 1}/{len(sampler.slots)}]"
                line = _judge_one(session, sampler, edge, slot, position)
                append_line(session.path, line)
                verdict = str(line["verdict"])
                judged += verdict != SKIP
            if verdict != SKIP:
                break


def _judge_one(session: _Session, sampler: Sampler, edge: ReviewEdge, slot: int,
               position: str) -> dict[str, object]:
    evidence = sampler.evidence_for(edge)
    text = session.chunks[evidence.chunk_id]
    def show() -> None:
        show_triple(session.out, session.candidates, edge, evidence, text, position)

    show()
    start = session.clock()
    looked_again = False
    while True:
        verdict = _ask_choice(session.ask, "[c]orrect, [w]rong, [s]kip, [q]uit: ", _VERDICTS)
        seconds = session.clock() - start
        if verdict == SKIP or looked_again or seconds >= FAST_SECONDS:
            break
        again = _ask(session.ask, f"That took {seconds:.0f} s. Take another look? [y/N]: ")
        if not again.lower().startswith("y"):
            break
        looked_again = True
        show()
    reason = (_ask_text(session.ask, "Reason it is wrong (one line; Ctrl-D quits): ")
              if verdict == WRONG else None)
    return session.line(
        JUDGMENT, slot=slot, slot_type=sampler.slots[slot], edge_id=edge.edge_id,
        triple={"type": edge.type, "start": dict(edge.start), "end": dict(edge.end),
                "properties": dict(edge.properties)},
        evidence={"chunk_id": evidence.chunk_id, "span": evidence.span,
                  "confidence": evidence.confidence, "extract_prompt": evidence.extract_prompt},
        evidence_chunks=len(edge.evidence), confidence=evidence.confidence,
        prompt_version=evidence.extract_prompt, verdict=verdict, reason=reason,
        seconds=seconds, looked_again=looked_again,
    )


def _miss_check(session: _Session, sampler: Sampler, count: int, done: set[str]) -> None:
    rng = random.Random(f"{sampler.rng_tag}:miss")
    chosen = rng.sample(sorted(session.chunks), min(count, len(session.chunks)))
    for i, chunk_id in enumerate(chosen, start=1):
        if chunk_id in done:
            continue
        show_chunk(session.out, session.candidates, chunk_id, session.chunks[chunk_id],
                   f"Miss check [{i}/{len(chosen)}]")
        start = session.clock()
        missing = _ask_choice(session.ask, "Anything important missing? [y]es, [n]o, [q]uit: ",
                              {"y": "y", "n": "n"}) == "y"
        seconds = session.clock() - start
        note = (_ask_text(session.ask, "What is missing (one line; Ctrl-D quits): ")
                if missing else None)
        append_line(session.path, session.line(MISS_CHECK, chunk_id=chunk_id, missing=missing,
                                                note=note, seconds=seconds))


# ── Command ──────────────────────────────────────────────────────────────────

def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Judge a run's extracted edges.")
    parser.add_argument("--reviewer", required=True, help="your name; recorded on each answer")
    parser.add_argument("--accession", required=True, type=_accession,
                        help="the filing's accession number")
    parser.add_argument("--run", type=int, help="the run to review (default: the latest)")
    return parser.parse_args(argv)


def _accession(value: str) -> str:
    if not _ACCESSION.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not an accession number like "
                                         "0001045810-26-000021")
    return value


def _candidates_file(directory: Path, accession_no: str, run: int | None) -> Path:
    """The run's candidates file: *run*, or the highest-numbered run of *accession_no*."""
    runs = [(n, p) for n, p in numbered_files(directory, accession_no, "run")
            if run is None or n == run]
    if not runs:
        which = f"run {run}" if run is not None else "any run"
        raise ValueError(f"no candidates file for {accession_no} ({which}) in {directory}; "
                         "run python -m extract.run first")
    return runs[-1][1]


def _chunk_texts(connect: Connect, accession_no: str) -> dict[str, str]:
    url = os.environ.get(DATABASE_ENV)
    if not url:
        raise ValueError(f"{DATABASE_ENV} not set (run with: uv run --env-file .env "
                         "python -m extract.review ...)")
    try:
        with connect(url) as conn:
            return {t.chunk_id: t.text for t in filing_chunk_texts(conn, accession_no)}
    except psycopg.Error as exc:  # the message can quote the URL, so only the type is shown
        raise ValueError(f"could not read the chunks: database error "
                         f"({type(exc).__name__})") from exc


def main(argv: Sequence[str] | None = None, *, connect: Connect = psycopg.connect,
         candidates_dir: Path = CANDIDATES_DIR, rounds_dir: Path = REPORTS_DIR) -> int:
    """CLI entry point; see the module docstring for the exit codes."""
    args = _parse_args(argv)
    if not sys.stdin.isatty():
        # Piped answers would let a script stand in for the researcher's judgment.
        print("extract.review needs an interactive terminal: a person judges each edge",
              file=sys.stderr)
        return EXIT_USAGE
    try:
        candidates = load_candidates(_candidates_file(candidates_dir, args.accession, args.run))
        chunks = _chunk_texts(connect, args.accession)
        summary = review(candidates, chunks, rounds_dir, reviewer=args.reviewer,
                         commit=git_state(), ask=input, out=print, clock=time.monotonic)
    except (OSError, ValueError) as exc:
        print(f"cannot review: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    if summary is not None:
        show_summary(print, summary)
        print(f"wrote {round_path(rounds_dir, args.accession, int(summary['round'])).name}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
