"""Union-find: the groups of keys that links join (Step 9).

Kept apart from resolve.groups so that resolve.cut can use it too.
"""
from __future__ import annotations

from collections.abc import Hashable, Iterable
from typing import TypeVar

_K = TypeVar("_K", bound=Hashable)


def connected(links: Iterable[Iterable[_K]]) -> list[frozenset[_K]]:
    """The groups of keys joined by *links* (each an iterable of keys), sorted by smallest key."""
    parent: dict[_K, _K] = {}

    def find(key: _K) -> _K:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for link in links:
        keys = list(link)
        for key in keys:
            find(key)
        for other in keys[1:]:
            ra, rb = find(keys[0]), find(other)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    groups: dict[_K, set[_K]] = {}
    for key in parent:
        groups.setdefault(find(key), set()).add(key)
    return sorted((frozenset(g) for g in groups.values()), key=min)
