"""Review round files and their summary (Step 8).

A round is `benchmarks/extraction/{accession_no}-round{N}.jsonl` (committed):
one line per judgment (`kind` judgment, skips included with verdict `skip`;
`seconds` runs from showing the triple to the final verdict, a second look
included and typing the reason not),
one per miss-check chunk (`kind` miss_check), and a last `kind` summary line
once the round is finished. A round without a summary line is open, and the
review command resumes it. Every line is appended and flushed as it is
answered, so quitting loses nothing already answered.
"""
from __future__ import annotations

import json
import math
import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from graph.ontology import CONFIDENCE_LEVELS

JUDGMENT = "judgment"
MISS_CHECK = "miss_check"
SUMMARY = "summary"
CORRECT = "correct"
WRONG = "wrong"
SKIP = "skip"
Z_95 = 1.959964
FAST_SECONDS = 15.0  # an answer quicker than this is asked to take another look
# Fields each kind of line must hold; the file is committed and may be edited by hand.
_REQUIRED = {
    JUDGMENT: ("edge_id", "verdict", "confidence", "seconds", "reviewer", "prompt_version",
               "candidates_file", "candidates_sha256"),
    MISS_CHECK: ("chunk_id", "missing", "reviewer", "candidates_file", "candidates_sha256"),
    SUMMARY: (),
}


@dataclass(frozen=True)
class Round:
    number: int
    path: Path
    lines: tuple[Mapping[str, object], ...]

    @property
    def closed(self) -> bool:
        return any(line["kind"] == SUMMARY for line in self.lines)

    def of_kind(self, kind: str) -> list[Mapping[str, object]]:
        return [line for line in self.lines if line["kind"] == kind]

    def judged_edges(self) -> set[str]:
        """Edges given a verdict (skips are not judged)."""
        return {str(j["edge_id"]) for j in self.of_kind(JUDGMENT) if j["verdict"] != SKIP}


def round_path(directory: Path, accession_no: str, number: int) -> Path:
    return directory / f"{accession_no}-round{number}.jsonl"


def numbered_files(directory: Path, accession_no: str, kind: str) -> list[tuple[int, Path]]:
    """``(N, path)`` of each ``{accession_no}-{kind}{N}.jsonl`` in *directory*, by N."""
    pattern = re.compile(rf"{re.escape(accession_no)}-{re.escape(kind)}(\d+)\.jsonl")
    return sorted((int(m[1]), p) for p in (directory.iterdir() if directory.is_dir() else ())
                  if (m := pattern.fullmatch(p.name)))


def read_rounds(directory: Path, accession_no: str) -> list[Round]:
    """Every round of *accession_no* in *directory*, by number; ValueError on a bad line."""
    return [Round(number, path, _read(path))
            for number, path in numbered_files(directory, accession_no, "round")]


def _read(path: Path) -> tuple[Mapping[str, object], ...]:
    lines = []
    for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            line = json.loads(text)
            if line["kind"] not in _REQUIRED:
                raise ValueError(f"unknown kind {line['kind']!r}")
            if missing := [f for f in _REQUIRED[line["kind"]] if f not in line]:
                raise ValueError(f"no {', '.join(missing)}")
        except (ValueError, KeyError, TypeError) as exc:
            # The file is committed evidence: a cut-off line is for a person to look at.
            raise ValueError(f"{path.name} line {number} is unreadable ({exc}); fix or remove "
                             "it, then resume") from exc
        lines.append(line)
    return tuple(lines)


def append_line(path: Path, line: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()


def wilson(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """The Wilson score interval for *successes* of *n*."""
    if n <= 0 or not 0 <= successes <= n:
        raise ValueError(f"cannot take an interval of {successes} of {n}")
    p = successes / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    denominator = 1 + z * z / n
    return (centre - spread) / denominator, (centre + spread) / denominator


def _accuracy(lines: Sequence[Mapping[str, object]]) -> dict[str, object]:
    correct = sum(1 for j in lines if j["verdict"] == CORRECT)
    return {"judged": len(lines), "correct": correct,
            "accuracy": correct / len(lines) if lines else None}


def summarize(round_: Round, *, size: int, closed_at: str, commit: str) -> dict[str, object]:
    """The round's figures; the miss rate is directional (5 chunks)."""
    all_judgments = round_.of_kind(JUDGMENT)
    judged = [j for j in all_judgments if j["verdict"] != SKIP]
    misses = round_.of_kind(MISS_CHECK)
    missing = sum(1 for m in misses if m["missing"])
    overall = _accuracy(judged)
    by_level = {level: _accuracy([j for j in judged if j["confidence"] == level])
                for level in CONFIDENCE_LEVELS}
    return {
        "round": round_.number,
        "candidates_file": all_judgments[0]["candidates_file"] if all_judgments else None,
        "target": size,
        **overall,
        "wrong": len(judged) - int(overall["correct"]),
        "skipped": len(all_judgments) - len(judged),
        "wilson_95": list(wilson(int(overall["correct"]), len(judged))) if judged else None,
        "by_confidence": {level: v for level, v in by_level.items() if v["judged"]},
        "by_confidence_label": "uncalibrated",
        "median_seconds": statistics.median(float(j["seconds"]) for j in judged)
        if judged else None,
        "fast_answers": sum(1 for j in judged if float(j["seconds"]) < FAST_SECONDS),
        "fast_seconds": FAST_SECONDS,
        "miss_check": {"chunks": len(misses), "missing": missing,
                       "rate": missing / len(misses) if misses else None,
                       "label": "directional"},
        "reviewers": sorted({str(j["reviewer"]) for j in (*all_judgments, *misses)}),
        "prompt_versions": sorted({str(j["prompt_version"]) for j in judged}),
        "commit": commit,
        "closed_at": closed_at,
    }
