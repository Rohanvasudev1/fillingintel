"""Communities at two levels over the community graph (Step 12).

find_communities() is pure. Leiden (graspologic-native, pinned exactly because
another version gives other clusters for the same seed) runs once over the
whole community graph for the top level, then once inside each top community
of more than `split_over` members, on the edges among its members, for the
lower level. A top community at or under the limit is its own lower
community. Every call uses the same settings and seed, and edges are passed
sorted, so the same graph gives the same communities on any run.

A community's `member_hash` is the SHA-256 of its sorted member keys, and its
`community_id` is its level plus the hash's first 12 characters. Communities
under `min_report_members` are kept but not reportable. The settings are fixed
before any question runs and never tuned on eval questions.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Literal, TypeAlias

import graspologic_native

from graph.community_graph import CommunityGraph, Pair

Level: TypeAlias = Literal["top", "lower"]
TOP: Level = "top"
LOWER: Level = "lower"
ID_HASH_CHARS = 12


@dataclass(frozen=True, slots=True)
class CommunitySettings:
    resolution: float = 1.0
    randomness: float = 0.001
    iterations: int = 1
    use_modularity: bool = True
    seed: int = 0xDEADBEEF
    split_over: int = 25  # a top community over this many members is split again
    min_report_members: int = 3

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def sha256(self) -> str:
        text = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()


DEFAULT_SETTINGS = CommunitySettings()


@dataclass(frozen=True, slots=True)
class Community:
    community_id: str
    level: Level
    members: tuple[str, ...]  # sorted keys
    member_hash: str
    parent: str | None  # the top community of a lower one
    reportable: bool


class CommunityError(RuntimeError):
    """Leiden returned something the two-level rule cannot use."""


def member_hash(keys: Iterable[str]) -> str:
    """SHA-256 of the sorted keys as compact JSON."""
    text = json.dumps(sorted(keys), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def find_communities(
    graph: CommunityGraph, settings: CommunitySettings = DEFAULT_SETTINGS,
) -> tuple[Community, ...]:
    """Top communities, largest first, then lower ones in their parents' order."""
    if not graph.weights:
        return ()
    tops = [_community(TOP, group, None, settings)
            for group in _leiden(graph.weights, set(graph.labels), settings)]
    tops.sort(key=lambda c: (-len(c.members), c.community_id))
    lowers = []
    for top in tops:
        groups = ([top.members] if len(top.members) <= settings.split_over
                  else _leiden(_inside(graph.weights, set(top.members)), set(top.members),
                               settings))
        lowers += sorted((_community(LOWER, g, top.community_id, settings) for g in groups),
                         key=lambda c: (-len(c.members), c.community_id))
    found = (*tops, *lowers)
    if len({c.community_id for c in found}) != len(found):
        raise CommunityError("two communities share a community_id; raise ID_HASH_CHARS")
    return found


def _community(
    level: Level, members: Iterable[str], parent: str | None, settings: CommunitySettings,
) -> Community:
    keys = tuple(sorted(members))
    digest = member_hash(keys)
    return Community(f"{level}-{digest[:ID_HASH_CHARS]}", level, keys, digest, parent,
                     len(keys) >= settings.min_report_members)


def _inside(weights: Mapping[Pair, int], members: set[str]) -> dict[Pair, int]:
    return {pair: w for pair, w in weights.items() if pair[0] in members and pair[1] in members}


def _leiden(
    weights: Mapping[Pair, int], nodes: set[str], settings: CommunitySettings,
) -> list[tuple[str, ...]]:
    """Leiden's groups over *weights*; CommunityError unless they cover exactly *nodes*."""
    edges = [(a, b, float(w)) for (a, b), w in sorted(weights.items())]
    _, membership = graspologic_native.leiden(
        edges=edges, starting_communities=None, resolution=settings.resolution,
        randomness=settings.randomness, iterations=settings.iterations,
        use_modularity=settings.use_modularity, seed=settings.seed, trials=1,
    )
    if set(membership) != nodes:
        raise CommunityError(f"Leiden placed {len(membership)} nodes of {len(nodes)}")
    groups: defaultdict[int, list[str]] = defaultdict(list)
    for node, cluster in membership.items():
        groups[cluster].append(node)
    return [tuple(sorted(g)) for g in groups.values()]
