"""Union-find over merged pairs, the canonical-key rule and decision groups (Step 9).

Both the pipeline and replay() group keys here, so a group read back from the
log gets the same canonical key the run chose. group_decisions() turns the
adjudicator's decisions into groups to merge or to hold back.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from resolve.decisions import Answer, HeldBackGroup, PairDecision

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


# ── Groups of adjudicated decisions ───────────────────────────────────────────

MAX_GROUP_NAMES = 5


@dataclass(frozen=True, slots=True)
class DecisionGroup:
    """Keys joined by merging decisions (and the deterministic groups they touch)."""

    keys: tuple[str, ...]
    merging: tuple[PairDecision, ...]  # the `same` decisions that join it, by ID


def group_decisions(decisions: Sequence[PairDecision], joined: Iterable[Iterable[str]] = (),
                    max_names: int = MAX_GROUP_NAMES
                    ) -> tuple[tuple[DecisionGroup, ...], tuple[HeldBackGroup, ...]]:
    """Union-find over the merging decisions plus *joined* (deterministic groups).

    A group with no merging decision is left alone. A group holding a `related`
    or `distinct` decision between two of its keys, or more than *max_names*
    keys, is held back with every decision among its keys; the rest come back
    as clean groups.
    """
    merging = [d for d in decisions if d.merges]
    clean: list[DecisionGroup] = []
    held: list[HeldBackGroup] = []
    for group in connected([*([d.key_a, d.key_b] for d in merging), *joined]):
        inside = tuple(sorted((d for d in decisions if d.key_a in group and d.key_b in group),
                              key=lambda d: d.decision_id))
        joining = tuple(d for d in inside if d.merges)
        if not joining:
            continue
        keys = tuple(sorted(group))
        if reason := _hold_reason(keys, inside, max_names):
            held.append(HeldBackGroup(keys=keys, reason=reason, decisions=inside))
        else:
            clean.append(DecisionGroup(keys=keys, merging=joining))
    return tuple(clean), tuple(held)


def _hold_reason(keys: Sequence[str], inside: Sequence[PairDecision], max_names: int) -> str:
    """Why the group is held back, every reason joined by "; ", or "" when it is clean."""
    reasons = []
    against = sum(d.answer in (Answer.RELATED, Answer.DISTINCT) for d in inside)
    if against:
        reasons.append(f"contradiction: {against} related or distinct "
                       f"{'pair' if against == 1 else 'pairs'} inside the group")
    if len(keys) > max_names:
        reasons.append(f"{len(keys)} names, more than {max_names}")
    return "; ".join(reasons)
