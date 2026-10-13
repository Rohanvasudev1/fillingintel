"""Where resolution reads and writes, and the alias map file (Step 9).

Outputs live under `benchmarks/resolution/` and are committed: the merge log,
the alias map replayed from it, and one report per run.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

from ingest.atomic_json import write_json_atomic
from ingest.provenance import REPO_ROOT

CANDIDATES_DIR = REPO_ROOT / "data" / "extract"
OUTPUT_DIR = REPO_ROOT / "benchmarks" / "resolution"
MERGE_LOG_NAME = "merge_log.jsonl"
ALIAS_MAP_NAME = "alias_map.json"
MERGE_LOG = OUTPUT_DIR / MERGE_LOG_NAME
ALIAS_MAP = OUTPUT_DIR / ALIAS_MAP_NAME
_CANDIDATES = re.compile(r"(\d{10}-\d{2}-\d{6})-run(\d+)\.jsonl")
_REPORT = re.compile(r"run(\d+)\.json")


def latest_candidates(directory: Path) -> tuple[Path, ...]:
    """The highest-numbered candidates file of each accession in *directory*, by accession."""
    latest: dict[str, tuple[int, Path]] = {}
    if directory.is_dir():
        for path in directory.iterdir():
            if m := _CANDIDATES.fullmatch(path.name):
                run = int(m[2])
                if run > latest.get(m[1], (0, path))[0]:
                    latest[m[1]] = (run, path)
    return tuple(path for _, (_, path) in sorted(latest.items()))


def next_report(directory: Path) -> Path:
    used = [int(m[1]) for p in directory.iterdir() if (m := _REPORT.fullmatch(p.name))] \
        if directory.is_dir() else []
    return directory / f"run{max(used, default=0) + 1}.json"


def read_alias_map(path: Path) -> dict[str, str]:
    """The alias map in *path*; an absent file is an empty map. ValueError if malformed."""
    if not path.exists():
        return {}
    return parse_alias_map(path.read_bytes(), path.name)


def parse_alias_map(raw: bytes, name: str = ALIAS_MAP_NAME) -> dict[str, str]:
    """The alias map in the file contents *raw*. ValueError if malformed."""
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise ValueError(f"{name} is not an object of key to canonical key")
    return data


def write_alias_map(path: Path, aliases: Mapping[str, str]) -> None:
    write_json_atomic(path, dict(sorted(aliases.items())), indent=2)



def next_numbered(directory: Path, prefix: str) -> Path:
    """`{prefix}{N}.json` in *directory*, N one past the highest used (from 1)."""
    pattern = re.compile(re.escape(prefix) + r"(\d+)\.json")
    used = [int(m[1]) for p in directory.iterdir() if (m := pattern.fullmatch(p.name))] \
        if directory.is_dir() else []
    return directory / f"{prefix}{max(used, default=0) + 1}.json"


def run_reports(directory: Path) -> tuple[Path, ...]:
    """The run reports in *directory*, highest-numbered first."""
    numbered = [(int(m[1]), p) for p in directory.iterdir() if (m := _REPORT.fullmatch(p.name))] \
        if directory.is_dir() else []
    return tuple(p for _, p in sorted(numbered, reverse=True))


def latest_report(directory: Path) -> Path | None:
    """The highest-numbered run report in *directory*, or None when there is none."""
    reports = run_reports(directory)
    return reports[0] if reports else None
