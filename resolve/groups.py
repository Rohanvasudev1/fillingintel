"""Union-find over merged pairs and the canonical-key rule (Step 9).

Both the pipeline and replay() group keys here, so a group read back from the
log gets the same canonical key the run chose.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

COMPANY_PREFIX = "Company:"


def canonical_key(chunk_counts: Mapping[str, int]) -> str:
    """A filer's Company key if present, else the key with most evidence chunks, ties smallest."""
    if not chunk_counts:
        raise ValueError("a group needs at least one key")
    return min(chunk_counts, key=lambda k: (not k.startswith(COMPANY_PREFIX),
                                            -chunk_counts[k], k))


def connected(links: Iterable[Iterable[str]]) -> list[frozenset[str]]:
    """The groups of keys joined by *links* (each an iterable of keys), sorted by smallest key."""
    parent: dict[str, str] = {}

    def find(key: str) -> str:
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
    groups: dict[str, set[str]] = {}
    for key in parent:
        groups.setdefault(find(key), set()).add(key)
    return sorted((frozenset(g) for g in groups.values()), key=min)
