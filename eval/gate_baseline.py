"""The gate baseline file, ``benchmarks/gate_baseline.json`` (Step 6, ADR-0003).

It holds the gated numbers and n per class, and what they were measured
against: the commit, the snapshot's SHA-256, the gated questions' hash, the
embedding model and k.  The gate refuses to compare against a baseline whose
inputs differ from its own; that is bad input, never a pass.

``python -m eval.gate --update-baseline`` rewrites it.  Agents never lower it to
get a change through (invariant 5): a lowering needs the user's yes in chat and
a BUILD-LOG line.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from eval.gate_scores import GATED_METRICS, WRONG_EVIDENCE, ClassScores
from eval.question_sets import AGENT_DRAFTED_LABEL
from ingest.atomic_json import write_json_atomic
from ingest.provenance import REPO_ROOT

BASELINE_PATH = REPO_ROOT / "benchmarks" / "gate_baseline.json"
_INDENT = 2
_SHOWN_DECIMALS = 6
_INPUTS = ("snapshot_sha256", "question_set_sha256", "embedding_model", "k")


class BaselineError(ValueError):
    """The gate baseline file is missing or invalid."""


class GateBaseline(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    arm: str
    split: Literal["dev"]
    commit: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    question_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_model: str = Field(min_length=1)
    k: int = Field(ge=1)
    scores: dict[str, ClassScores]  # gated classes in order, then pooled

    @field_validator("label")
    @classmethod
    def agent_drafted(cls, v: str) -> str:
        if v != AGENT_DRAFTED_LABEL:
            raise ValueError(f"the gate scores only {AGENT_DRAFTED_LABEL!r}, not {v!r}")
        return v


def read_baseline(path: Path = BASELINE_PATH) -> GateBaseline:
    """The baseline at *path*; raises ``BaselineError`` if it is missing or invalid."""
    try:
        return GateBaseline.model_validate_json(path.read_bytes())
    except FileNotFoundError as exc:
        raise BaselineError(
            f"no gate baseline at {path}; create one with python -m eval.gate --update-baseline"
        ) from exc
    except (OSError, ValidationError) as exc:
        raise BaselineError(f"cannot read the gate baseline {path.name}: {exc}") from exc


def write_baseline(path: Path, baseline: GateBaseline) -> None:
    """Write *baseline* atomically, indented so an update reads as a plain diff."""
    write_json_atomic(path, baseline.model_dump(mode="json", by_alias=True), indent=_INDENT)


def baseline_mismatches(
    baseline: GateBaseline,
    *,
    snapshot_sha256: str,
    question_set_sha256: str,
    embedding_model: str,
    k: int,
) -> list[str]:
    """One line per input that differs from what *baseline* was measured against."""
    current = {"snapshot_sha256": snapshot_sha256, "question_set_sha256": question_set_sha256,
               "embedding_model": embedding_model, "k": k}
    return [
        f"{name} is {current[name]}, but the gate baseline was measured with "
        f"{getattr(baseline, name)}"
        for name in _INPUTS
        if current[name] != getattr(baseline, name)
    ]


def _shown(value: float | None) -> str:
    return "none" if value is None else f"{value:.{_SHOWN_DECIMALS}f}"


def _value(scores: ClassScores | None, name: str) -> float | None:
    return None if scores is None else scores.model_dump(by_alias=True)[name]


def _lowered(metric: str, old: float | None, new: float | None) -> bool:
    if old is None or new is None:
        return old is not None
    return new > old if metric == WRONG_EVIDENCE else new < old


def baseline_changes(old: GateBaseline | None, new: GateBaseline) -> list[str]:
    """What *new* changes from *old*: each moved number, then each changed input or commit."""
    if old is None:
        return ["no previous gate baseline"]
    lines = []
    for cls in dict.fromkeys([*old.scores, *new.scores]):
        before, after = old.scores.get(cls), new.scores.get(cls)
        if _value(before, "n") != _value(after, "n"):
            lines.append(f"{cls} n: {_value(before, 'n')} -> {_value(after, 'n')}")
        for metric in GATED_METRICS:
            a, b = _value(before, metric), _value(after, metric)
            if a != b:
                flag = " LOWERED" if _lowered(metric, a, b) else ""
                lines.append(f"{cls} {metric}: {_shown(a)} -> {_shown(b)}{flag}")
    lines += [f"{name}: {getattr(old, name)} -> {getattr(new, name)}"
              for name in ("commit", *_INPUTS) if getattr(old, name) != getattr(new, name)]
    return lines
