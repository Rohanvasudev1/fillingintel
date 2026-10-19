"""Which dev answers the user scores, drawn once by a fixed seed (Step 13 ticket 04).

Two kinds of answer are drawn, each stratified by arm and class:

- answers to answerable questions with at least one kept sentence (faithfulness,
  relevancy and citation support);
- answers to decline and unanswerable questions (decline and not-found correctness).

Within a kind, the strata are put in an order by the seed, each stratum's
answers are shuffled by the seed, and answers are taken one per stratum in turn
until the kind's count is reached, so every stratum gets one before any gets
two.  A question is drawn at most once across arms, so the sample spreads over
questions; ``exclude_questions`` keeps another draw's questions out (the
corruption test's draw is disjoint from the calibration draw).

``write_draw`` writes ``benchmarks/calibration/draw.json`` once: rewriting the
same draw keeps the file, and a different draw is refused.

    uv run python -m eval.calibration.draw
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

from eval.calibration.answers import (
    CALIBRATION_RUNS,
    RUNS_DIR,
    AnswerFileError,
    Kind,
    RunAnswer,
    load_answers,
)
from eval.calibration.scores import CALIBRATION_DIR
from ingest.atomic_json import write_json_atomic
from ingest.provenance import git_state

DRAW_PATH = CALIBRATION_DIR / "draw.json"
CALIBRATION_SEED = 20261016  # the grilling date
ANSWERS, BEHAVIOURS = 24, 6
LABEL = "agent-drafted questions, dev; drawn for calibration"


class DrawError(ValueError):
    """The draw cannot be made, or the draw file holds another draw or cannot be read."""


@dataclass(frozen=True)
class DrawEntry:
    key: str
    arm: str
    run_file: str
    question_id: str
    class_: str
    kind: Kind


@dataclass(frozen=True)
class DrawFile:
    seed: int
    entries: tuple[DrawEntry, ...]


def _eligible(answer: RunAnswer, kind: Kind) -> bool:
    if answer.kind != kind:
        return False
    return bool(answer.kept) if kind == "answer" else True


def _take(pool: Sequence[RunAnswer], kind: Kind, count: int, rng: random.Random,
          taken: set[str]) -> list[RunAnswer]:
    strata: dict[tuple[str, str], list[RunAnswer]] = {}
    for answer in pool:
        if _eligible(answer, kind):
            strata.setdefault((answer.arm, answer.class_), []).append(answer)
    order = sorted(strata)
    rng.shuffle(order)
    queues = {s: rng.sample(strata[s], len(strata[s])) for s in order}
    chosen: list[RunAnswer] = []
    while len(chosen) < count and any(queues.values()):
        for stratum in order:
            queue = queues[stratum]
            while queue and queue[0].question_id in taken:
                queue.pop(0)
            if queue and len(chosen) < count:
                answer = queue.pop(0)
                chosen.append(answer)
                taken.add(answer.question_id)
    if len(chosen) < count:
        raise DrawError(f"only {len(chosen)} {kind} answers can be drawn; {count} wanted")
    return chosen


def draw(pool: Sequence[RunAnswer], seed: int, answers: int = ANSWERS,
         behaviours: int = BEHAVIOURS,
         exclude_questions: Collection[str] = frozenset()) -> tuple[RunAnswer, ...]:
    """*answers* answerable answers, then *behaviours* decline and unanswerable answers."""
    rng = random.Random(seed)
    taken = set(exclude_questions)
    return (*_take(pool, "answer", answers, rng, taken),
            *_take(pool, "behaviour", behaviours, rng, taken))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(drawn: Sequence[RunAnswer], seed: int) -> dict[str, object]:
    return {
        "label": LABEL,
        "seed": seed,
        "sources": {arm: {"file": name, "sha256": _sha256(RUNS_DIR / name)}
                    for arm, name in CALIBRATION_RUNS.items()},
        "entries": [{"key": a.key, "arm": a.arm, "run_file": a.run_file,
                     "question_id": a.question_id, "class": a.class_, "kind": a.kind}
                    for a in drawn],
    }


def write_draw(path: Path, drawn: Sequence[RunAnswer], seed: int, commit: str) -> bool:
    """Write the draw file; False if it already holds this draw; DrawError if another."""
    record = _record(drawn, seed)
    if path.exists():
        kept = _read_json(path)
        if {k: kept.get(k) for k in record} != record:
            raise DrawError(f"{path.name} holds another draw; it is written once")
        return False
    write_json_atomic(path, {**record, "commit": commit}, indent=2)
    return True


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DrawError(f"cannot read {path.name}: {type(exc).__name__}") from exc


def read_draw(path: Path = DRAW_PATH) -> DrawFile:
    data = _read_json(path)
    try:
        return DrawFile(seed=int(data["seed"]), entries=tuple(
            DrawEntry(e["key"], e["arm"], e["run_file"], e["question_id"], e["class"],
                      e["kind"]) for e in data["entries"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise DrawError(f"{path.name} is malformed: {type(exc).__name__}: {exc}") from exc


def drawn_answers(draw_file: DrawFile, pool: Sequence[RunAnswer]) -> tuple[RunAnswer, ...]:
    """The pool's answers for the draw's entries, in draw order."""
    found = {a.key: a for a in pool}
    missing = [e.key for e in draw_file.entries if e.key not in found]
    if missing:
        raise DrawError(f"the run files lack drawn answers: {missing[:5]}")
    return tuple(found[e.key] for e in draw_file.entries)


def main() -> int:
    """``python -m eval.calibration.draw``: write the calibration draw (offline, once)."""
    try:
        drawn = draw(load_answers(), CALIBRATION_SEED)
        written = write_draw(DRAW_PATH, drawn, CALIBRATION_SEED, git_state())
    except (AnswerFileError, DrawError, OSError) as exc:
        print(f"could not draw: {exc}", file=sys.stderr)
        return 3
    print(f"{'wrote' if written else 'kept'} {DRAW_PATH} ({len(drawn)} answers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
