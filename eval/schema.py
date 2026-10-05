"""Eval-set records and their JSONL files (Step 4).

One record per line.  The schema borrows FinRank's per-record labels (topic,
difficulty, reasoning, evidence scope, hard negatives labelled by relation)
and pins each gold chunk's text by SHA-256, so a re-chunk that changes gold
evidence fails validation instead of silently moving the target.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

Class = Literal["lookup", "local", "multi_hop", "global", "decline", "unanswerable"]
NO_GOLD_CLASSES = frozenset({"decline", "unanswerable"})
Ticker = Literal["NVDA", "AMD", "INTC"]
Provenance = Literal["human_written", "human_verified", "agent_drafted"]
HUMAN_PROVENANCE = frozenset({"human_written", "human_verified"})
Relation = Literal["same_company_other_period", "same_company_same_filing", "peer_company"]
MAX_HARD_NEGATIVES = 2
TEST_SPLIT_PERCENT = 30  # records whose question hashes into this share are held out
DRAFT_KEY_CHARS = 16

_ID = re.compile(r"^q\d{4}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NonBlank = Annotated[str, Field(min_length=1, pattern=r"\S")]


class HardNegative(BaseModel):
    """A confusable chunk a correct answer must not rest on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: str
    relation: Relation


class EvalRecord(BaseModel):
    """One eval question with its gold answer and gold evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    id: str
    question: _NonBlank
    class_: Class = Field(alias="class")
    gold_answer: _NonBlank
    gold_chunk_ids: list[str]
    gold_text_sha256: dict[str, str]
    hard_negatives: list[HardNegative] = Field(default_factory=list, max_length=MAX_HARD_NEGATIVES)
    tickers: list[Ticker] = Field(min_length=1)
    fiscal_periods: list[str] = Field(min_length=1)
    topic: _NonBlank
    difficulty: Literal["easy", "medium", "hard"]
    reasoning: Literal["quantitative", "qualitative"]
    evidence_scope: Literal["none", "single", "two", "multi"]
    provenance: Provenance
    author: _NonBlank
    split: Literal["dev", "test"]
    derived_from: str | None = None  # draft_key() of the draft a human_verified record came from
    notes: str = ""

    @field_validator("id")
    @classmethod
    def id_format(cls, v: str) -> str:
        if not _ID.fullmatch(v):
            raise ValueError(f"id must look like q0001, got {v!r}")
        return v

    @model_validator(mode="after")
    def gold_is_consistent(self) -> EvalRecord:
        if self.class_ in NO_GOLD_CLASSES:
            if self.gold_chunk_ids:
                raise ValueError(f"class {self.class_} has no gold chunks")
        elif not self.gold_chunk_ids:
            raise ValueError(f"class {self.class_} needs at least one of gold_chunk_ids")
        if len(set(self.gold_chunk_ids)) != len(self.gold_chunk_ids):
            raise ValueError("gold_chunk_ids repeat a chunk")
        if set(self.gold_text_sha256) != set(self.gold_chunk_ids):
            raise ValueError("gold_text_sha256 must have exactly one entry per gold chunk")
        if not all(_SHA256.fullmatch(h) for h in self.gold_text_sha256.values()):
            raise ValueError("gold_text_sha256 values must be 64 lowercase hex characters")
        negatives = [n.chunk_id for n in self.hard_negatives]
        if self.class_ in NO_GOLD_CLASSES and negatives:
            raise ValueError(f"class {self.class_} has no hard negatives")
        if len(set(negatives)) != len(negatives):
            raise ValueError("hard negatives repeat a chunk")
        overlap = set(negatives) & set(self.gold_chunk_ids)
        if overlap:
            raise ValueError(f"hard negative is also a gold chunk: {sorted(overlap)}")
        return self

    @model_validator(mode="after")
    def derived_from_matches_provenance(self) -> EvalRecord:
        """A ``human_verified`` record names the draft it came from; nothing else does."""
        if self.provenance == "human_verified" and not self.derived_from:
            raise ValueError("a human_verified record needs derived_from (its draft key)")
        if self.provenance != "human_verified" and self.derived_from:
            raise ValueError(f"a {self.provenance} record must not have derived_from")
        return self


def normalise_question(question: str) -> str:
    """Lower case, single spaces: the form used to compare questions."""
    return " ".join(question.lower().split())


def draft_key(record: EvalRecord) -> str:
    """Stable identity of a draft: its question (case and spacing ignored) and gold chunks.

    Draft IDs change when slices are re-merged; this key does not, so review
    decisions and ``derived_from`` survive renumbering.
    """
    payload = normalise_question(record.question) + "|" + ",".join(sorted(record.gold_chunk_ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:DRAFT_KEY_CHARS]


def split_for(question: str) -> str:
    """``"test"`` for a stable ~30% of questions (by hash of the text), else ``"dev"``."""
    bucket = int(hashlib.sha256(normalise_question(question).encode("utf-8")).hexdigest(), 16) % 100
    return "test" if bucket < TEST_SPLIT_PERCENT else "dev"


def load_records(path: Path) -> list[EvalRecord]:
    """Read a JSONL eval file; errors name the line.  IDs must be unique."""
    return parse_records(path.read_text(encoding="utf-8"), path.name)


def parse_records(text: str, name: str) -> list[EvalRecord]:
    """Parse JSONL eval records from *text*; errors name *name* and the line."""
    records: list[EvalRecord] = []
    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = EvalRecord.model_validate_json(line)
        except ValidationError as exc:
            raise ValueError(f"{name} line {number}: {exc}") from exc
        if record.id in seen:
            raise ValueError(f"{name} line {number}: duplicate id {record.id}")
        seen.add(record.id)
        records.append(record)
    return records


def dump_records(records: list[EvalRecord], path: Path) -> None:
    """Write *records* as JSONL (field ``class`` by its alias), replacing the file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(r.model_dump_json(by_alias=True) + "\n" for r in records)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(fh.name, path)
