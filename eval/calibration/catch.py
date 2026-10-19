"""How often the judge catches each planted error (Step 13 ticket 04).

Per corrupted copy:

- faithfulness catches the error when the judge's mean over its runs is below its
  mean for the uncorrupted answer (the run file's three scores);
- citation support catches it when most runs mark the corrupted (or added)
  sentence not supported.  How often the judge had already rejected the same
  sentence before the corruption is counted beside it, since that catch says
  little.

A copy with no score on a metric is counted as unscored, never as caught.
Rates carry Wilson 95% intervals.  This is the corruption test, not calibration.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean

from eval.calibration.corruptors import KINDS, CorruptionKind
from eval.calibration.judge_verdicts import Verdicts, majority
from extract.review_round import wilson

LABEL = "corruption test, not calibration"


@dataclass(frozen=True)
class CopyResult:
    key: str
    kind: CorruptionKind
    sentence_index: int
    original_faithfulness: tuple[float | None, ...]
    corrupted_faithfulness: tuple[float | None, ...]
    original_verdicts: tuple[Verdicts, ...] | None  # None for an added sentence
    corrupted_verdicts: tuple[Verdicts, ...]


def _mean(values: Sequence[float | None]) -> float | None:
    found = [v for v in values if v is not None]
    return fmean(found) if found else None


def faithfulness_caught(copy: CopyResult) -> bool | None:
    before, after = _mean(copy.original_faithfulness), _mean(copy.corrupted_faithfulness)
    return None if before is None or after is None else after < before


def citation_caught(copy: CopyResult) -> bool | None:
    supported = majority(copy.corrupted_verdicts, copy.sentence_index)
    return None if supported is None else not supported


def already_rejected(copy: CopyResult) -> bool:
    if copy.original_verdicts is None:
        return False
    return majority(copy.original_verdicts, copy.sentence_index) is False


def _rate(caught: Sequence[bool | None]) -> dict[str, object]:
    scored = [c for c in caught if c is not None]
    hits = sum(scored)
    return {
        "scored": len(scored),
        "unscored": len(caught) - len(scored),
        "caught": hits,
        "rate": hits / len(scored) if scored else None,
        "wilson_95": list(wilson(hits, len(scored))) if scored else None,
    }


def catch_rates(copies: Sequence[CopyResult],
                skipped: Mapping[str, int]) -> dict[str, dict[str, object]]:
    """Per error kind, in ``KINDS`` order: copies made, skips, and both metrics' catches."""
    rows: dict[str, dict[str, object]] = {}
    for kind in KINDS:
        mine = [c for c in copies if c.kind == kind]
        faithful = _rate([faithfulness_caught(c) for c in mine])
        scored = [c for c in mine if faithfulness_caught(c) is not None]
        faithful["original_mean"] = _mean([_mean(c.original_faithfulness) for c in scored])
        faithful["corrupted_mean"] = _mean([_mean(c.corrupted_faithfulness) for c in scored])
        cited = _rate([citation_caught(c) for c in mine])
        cited["already_not_supported"] = sum(already_rejected(c) for c in mine
                                             if citation_caught(c))
        rows[kind] = {"applied": len(mine), "skipped": skipped.get(kind, 0),
                      "faithfulness": faithful, "citation_support": cited}
    return rows
