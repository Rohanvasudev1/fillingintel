"""Read a communities run file written by `python -m graph.communities` (Step 12).

The file is committed data, so it is checked at this boundary: every
community must carry the fields graph.community.Community has, with a level,
a parent for each lower community and a member hash that matches its
members. Anything else is a CommunitiesFileError naming the file.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from graph.community import LOWER, TOP, Community, member_hash

RUN_PREFIX = "communities-run"
_RUN = re.compile(rf"{RUN_PREFIX}(\d+)\.json")


class CommunitiesFileError(ValueError):
    """The communities file is missing or malformed; the message is safe to print."""


@dataclass(frozen=True, slots=True)
class CommunitiesFile:
    name: str  # such as communities-run1.json
    sha256: str
    input_sha256: str  # the community graph's input hash
    communities: tuple[Community, ...]


def latest_communities_file(directory: Path) -> Path:
    """The highest-numbered communities run file in *directory*."""
    runs = [(int(m[1]), p) for p in directory.glob(f"{RUN_PREFIX}*.json")
            if (m := _RUN.fullmatch(p.name))]
    if not runs:
        raise CommunitiesFileError(f"no {RUN_PREFIX}N.json in {directory.name}; run "
                                   "python -m graph.communities first")
    return max(runs)[1]


def read_communities_file(path: Path) -> CommunitiesFile:
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
        input_sha = data["community_graph"]["input_sha256"]
        communities = tuple(_community(c) for c in data["communities"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CommunitiesFileError(f"cannot read {path.name}: {type(exc).__name__}: {exc}") \
            from exc
    if not isinstance(input_sha, str):
        raise CommunitiesFileError(f"{path.name}: community_graph.input_sha256 is not a string")
    return CommunitiesFile(path.name, hashlib.sha256(raw).hexdigest(), input_sha, communities)


def _community(entry: dict[str, object]) -> Community:
    members = entry["members"]
    if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
        raise ValueError(f"{entry['community_id']}: members must be a list of keys")
    level, parent = entry["level"], entry["parent"]
    if level not in (TOP, LOWER) or (parent is None) != (level == TOP):
        raise ValueError(f"{entry['community_id']}: bad level or parent")
    if entry["member_hash"] != member_hash(members):
        raise ValueError(f"{entry['community_id']}: member_hash does not match its members")
    if not isinstance(entry["reportable"], bool):
        raise ValueError(f"{entry['community_id']}: reportable is not a boolean")
    return Community(str(entry["community_id"]), level, tuple(sorted(members)),
                     str(entry["member_hash"]), None if parent is None else str(parent),
                     entry["reportable"])
