"""Dev answers read back from the committed arm runs, for calibration and the corruption
test (Step 13 ticket 04).

One full dev run per arm: the four arms' own runs, not the router runs, which
replay those arms' answers.  An answer's kept sentences are rebuilt by running
citation enforcement again on its raw reply against its retrieved chunks, so
each sentence keeps its line break and the judge's requests can be rebuilt
exactly; a rebuild that disagrees with the file's kept sentences is an error, and
so is a run judged under another judge config than the pinned one.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from eval.corpus_index import ChunkIndex
from eval.judging.scoring import JudgeConfig
from eval.schema import NO_GOLD_CLASSES, Class
from ingest.provenance import REPO_ROOT
from retrieve.answer import read_status
from retrieve.citations import Sentence, enforce_citations

RUNS_DIR = REPO_ROOT / "benchmarks" / "runs"
# arm -> its full dev run; graph_global's answered only global, decline and unanswerable.
CALIBRATION_RUNS: Mapping[str, str] = MappingProxyType({
    "vector": "2026-10-05-vector-dev-96e8c69.json",
    "graph_local": "2026-10-14-graph_local-dev-375efac.json",
    "graph_traversal": "2026-10-14-graph_traversal-dev-375efac.json",
    "graph_global": "2026-10-16-graph_global-dev-5bb0799.json",
})
Kind = Literal["answer", "behaviour"]


class AnswerFileError(ValueError):
    """A run file cannot be read back into answers."""


@dataclass(frozen=True)
class RunAnswer:
    arm: str
    run_file: str
    question_id: str
    class_: Class
    question: str
    status: str
    text: str  # what a reader sees
    raw_text: str
    kept: tuple[Sentence, ...]
    retrieved: tuple[str, ...]  # chunk IDs in rank order: the chunks the answer model saw
    judged: Mapping[str, tuple[float | None, ...]]  # metric -> the judge's score per run
    judge_cost_usd: float  # the three judge runs over every metric, cached calls included

    @property
    def key(self) -> str:
        return f"{self.arm}:{self.question_id}"

    @property
    def kind(self) -> Kind:
        return "behaviour" if self.class_ in NO_GOLD_CLASSES else "answer"


def _kept(question: Mapping, retrieved: tuple[str, ...]) -> tuple[Sentence, ...]:
    answer = question["answer"]
    status, body = read_status(answer["raw_text"])
    check = enforce_citations("" if answer["status"] == "refused" else body, retrieved)
    stored = [(s["text"], tuple(s["citations"])) for s in answer["kept"]]
    if [(s.text, s.citations) for s in check.kept] != stored:
        raise AnswerFileError(f"{question['id']}: re-enforcing the raw reply does not give "
                              "the kept sentences the file holds")
    return check.kept


def _answer(arm: str, run_file: str, question: Mapping) -> RunAnswer:
    retrieved = tuple(r["chunk_id"] for r in question["retrieved"])
    judged = question["judged"]["metrics"]
    return RunAnswer(
        arm=arm, run_file=run_file, question_id=question["id"], class_=question["class"],
        question=question["question"], status=question["answer"]["status"],
        text=question["answer"]["text"], raw_text=question["answer"]["raw_text"],
        kept=_kept(question, retrieved), retrieved=retrieved,
        judged=MappingProxyType({m: tuple(v["runs"]) for m, v in judged.items()}),
        judge_cost_usd=float(question["judged"]["cost_usd"]),
    )


def load_run(path: Path, arm: str) -> list[RunAnswer]:
    """Every judged answer in the run file at *path*."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        header = document["header"]
        if header["split"] != "dev" or header["arm"] != arm:
            raise AnswerFileError(f"{path.name} is not a dev run of {arm}")
        if header["judging"] != JudgeConfig().as_header():
            raise AnswerFileError(f"{path.name} was judged under another judge config than "
                                  "the pinned one")
        return [_answer(arm, path.name, q) for q in document["questions"]
                if q.get("answer") is not None and q.get("judged") is not None]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, AnswerFileError):
            raise
        raise AnswerFileError(f"cannot read {path.name}: {type(exc).__name__}: {exc}") from exc


def load_answers(runs_dir: Path = RUNS_DIR,
                 runs: Mapping[str, str] = CALIBRATION_RUNS) -> list[RunAnswer]:
    """Every judged dev answer of every arm, by arm then file order."""
    return [a for arm, name in runs.items() for a in load_run(runs_dir / name, arm)]


def by_key(answers: Sequence[RunAnswer]) -> dict[str, RunAnswer]:
    return {a.key: a for a in answers}


def chunk_texts(answers: Sequence[RunAnswer], parsed_dir: Path) -> dict[str, str]:
    """The text of every chunk the answers retrieved; a chunk that does not resolve is an
    error, since the judges and the user must see what the answer model saw."""
    index = ChunkIndex.from_parsed_dir(parsed_dir)
    texts = {}
    for chunk_id in sorted({c for a in answers for c in a.retrieved}):
        text = index.text(chunk_id)
        if text is None:
            raise AnswerFileError(f"chunk {chunk_id} does not resolve in {parsed_dir}")
        texts[chunk_id] = text
    return texts
