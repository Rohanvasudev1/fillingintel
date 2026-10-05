"""The question sets a run scores, each reported in its own column (invariant 1).

``agent_drafted_set.jsonl`` is always read.  ``eval_set.jsonl`` is read only when
it has records, and every record in it must be human-written or human-verified;
every record in the agent-drafted file must be agent-drafted.  A mixed file is
refused, so no result can carry the wrong label.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from eval.schema import HUMAN_PROVENANCE, EvalRecord, parse_records

EVAL_DIR = Path(__file__).resolve().parent
AGENT_DRAFTED_FILE = "agent_drafted_set.jsonl"
HUMAN_FILE = "eval_set.jsonl"
AGENT_DRAFTED_LABEL = "agent-drafted questions"
HUMAN_LABEL = "human-written or human-verified questions"


class QuestionSetError(ValueError):
    """An eval file is missing, invalid, or holds records of the wrong provenance."""


@dataclass(frozen=True)
class QuestionSet:
    name: str  # the column name in the results: "agent_drafted" or "human"
    label: str
    file: str
    sha256: str  # of the whole file, as read
    records: tuple[EvalRecord, ...]  # every record in the file, all splits


def _read(path: Path) -> tuple[str, tuple[EvalRecord, ...]]:
    """The file's SHA-256 and its records, both from one read of the bytes."""
    try:
        raw = path.read_bytes()
        records = parse_records(raw.decode("utf-8"), path.name)
    except (OSError, ValueError) as exc:
        raise QuestionSetError(f"cannot read {path.name}: {exc}") from exc
    return hashlib.sha256(raw).hexdigest(), tuple(records)


def _check_provenance(path: Path, records: tuple[EvalRecord, ...], human: bool) -> None:
    wrong = [r.id for r in records if (r.provenance in HUMAN_PROVENANCE) != human]
    if wrong:
        expected = "human-written or human-verified" if human else "agent-drafted"
        raise QuestionSetError(
            f"{path.name} must hold only {expected} records; these are not: {wrong[:10]}"
        )


def load_question_sets(eval_dir: Path = EVAL_DIR) -> tuple[QuestionSet, ...]:
    """The agent-drafted set, then the human set if ``eval_set.jsonl`` has records."""
    agent_path = eval_dir / AGENT_DRAFTED_FILE
    sha, records = _read(agent_path)
    _check_provenance(agent_path, records, human=False)
    agent = QuestionSet("agent_drafted", AGENT_DRAFTED_LABEL, AGENT_DRAFTED_FILE, sha, records)
    human_path = eval_dir / HUMAN_FILE
    if not human_path.exists():
        return (agent,)
    sha, records = _read(human_path)
    if not records:
        return (agent,)
    _check_provenance(human_path, records, human=True)
    return (agent, QuestionSet("human", HUMAN_LABEL, HUMAN_FILE, sha, records))
