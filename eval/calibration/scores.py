"""The user's calibration scores: one JSON line per scored answer (Step 13 ticket 04).

``benchmarks/calibration/scores-{reviewer}.jsonl`` is append-only.  An entry
holds, for an answerable question's answer, each claim's label (``supported``,
``not_supported`` or ``not_a_claim``, with a reason for every ``not_supported``;
claims the user added have source ``added``), a citation-support verdict per
kept sentence and a relevancy rating from 1 to 5; for a decline or unanswerable
question's answer, whether it showed the expected behaviour.  It also records
how long the answer took and whether the pace check sent the user back.  A
re-score entry (``rescore: true``) is kept apart from the first pass.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from ingest.provenance import REPO_ROOT

CALIBRATION_DIR = REPO_ROOT / "benchmarks" / "calibration"
CLAIMS_FILE = "claims.jsonl"
FAST_SECONDS = 15.0  # an answer scored faster than this is offered another look
ClaimLabel = Literal["supported", "not_supported", "not_a_claim"]
LABELS: tuple[ClaimLabel, ...] = ("supported", "not_supported", "not_a_claim")
_SAFE = re.compile(r"[^a-z0-9]+")


class ScoresFileError(ValueError):
    """A scores file cannot be read."""


def reviewer_name(reviewer: str) -> str:
    """The reviewer as stored in entries; refuses an empty name."""
    name = reviewer.strip()
    if not name:
        raise ValueError("reviewer name is required")
    return name


def scores_path(reviewer: str, directory: Path = CALIBRATION_DIR) -> Path:
    """``scores-{reviewer}.jsonl``, the name lower-cased with runs of other characters as -."""
    slug = _SAFE.sub("-", reviewer_name(reviewer).lower()).strip("-")
    if not slug:
        raise ValueError("reviewer name is required")
    return directory / f"scores-{slug}.jsonl"


def append_entry(path: Path, entry: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()


def read_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    except (OSError, ValueError) as exc:
        raise ScoresFileError(f"cannot read {path.name}: {type(exc).__name__}") from exc


def scored_keys(entries: Sequence[Mapping], rescore: bool) -> set[str]:
    return {str(e["key"]) for e in entries if bool(e["rescore"]) is rescore}
