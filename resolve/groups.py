"""Union-find over merged pairs, the canonical-key rule and decision groups (Steps 9, 9b).

Both the pipeline and replay() group keys here, so a group read back from the
log gets the same canonical key the run chose. group_decisions() turns the
adjudicator's decisions into groups to merge or to hold back: a group holding
a `related` or `distinct` pair is cut into pieces (resolve.cut), and a group or
piece with more names than the limit is held back.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from resolve.cut import NoCut, smallest_cut
from resolve.decisions import Answer, CutGroup, HeldBackGroup, PairDecision
from resolve.unionfind import connected

__all__ = ["COMPANY_PREFIX", "GROUP_CUT_RULE", "DecisionGroup", "Grouping",
           "canonical_key", "connected", "group_decisions"]

COMPANY_PREFIX = "Company:"


def canonical_key(chunk_counts: Mapping[str, int]) -> str:
    """A filer's Company key if present, else the key with most evidence chunks, ties smallest."""
    if not chunk_counts:
        raise ValueError("a group needs at least one key")
    return min(chunk_counts, key=lambda k: (not k.startswith(COMPANY_PREFIX),
                                            -chunk_counts[k], k))


# ── Groups of adjudicated decisions ───────────────────────────────────────────

GROUP_CUT_RULE = "group_cut"  # the merge-log rule of a piece of a cut group
_AGAINST = (Answer.RELATED, Answer.DISTINCT)


@dataclass(frozen=True, slots=True)
class DecisionGroup:
    """Keys joined by merging decisions (and the deterministic groups they touch)."""

    keys: tuple[str, ...]
    merging: tuple[PairDecision, ...]  # the `same` decisions that join it, by ID
    cut: bool = False  # a piece of a cut group


@dataclass(frozen=True, slots=True)
class Grouping:
    clean: tuple[DecisionGroup, ...]  # groups and pieces to merge
    held_back: tuple[HeldBackGroup, ...]  # groups and pieces not merged, with the reason
    cut_groups: tuple[CutGroup, ...]  # every group that was cut


def group_decisions(decisions: Sequence[PairDecision], joined: Iterable[Iterable[str]],
                    max_names: int) -> Grouping:
    """Union-find over the merging decisions plus *joined* (deterministic groups).

    A group with no merging decision is left alone. A group with no `related` or
    `distinct` decision between two of its keys merges whole, unless it has more
    than *max_names* keys (ResolveConfig.max_group_names). Any other group is cut
    (resolve.cut) and each piece joined by a kept `same` decision merges, unless
    it is over *max_names*.
    """
    deterministic = [tuple(keys) for keys in joined]
    merging = [d for d in decisions if d.merges]
    outcomes: list[DecisionGroup | HeldBackGroup | None] = []
    cuts: list[CutGroup] = []
    for group in connected([*([d.key_a, d.key_b] for d in merging), *deterministic]):
        inside = _among(decisions, group)
        if not any(d.merges for d in inside):
            continue
        keys = tuple(sorted(group))
        if not any(d.answer in _AGAINST for d in inside):
            outcomes.append(_piece(keys, inside, False, max_names))
            continue
        cut = _cut(keys, inside, [k for k in deterministic if group.issuperset(k)], max_names)
        if isinstance(cut, HeldBackGroup):
            outcomes.append(cut)
            continue
        cuts.append(cut)
        outcomes += [_piece(p, _among(inside, p), True, max_names) for p in cut.pieces]
    return Grouping(clean=tuple(o for o in outcomes if isinstance(o, DecisionGroup)),
                    held_back=tuple(o for o in outcomes if isinstance(o, HeldBackGroup)),
                    cut_groups=tuple(cuts))


def _cut(keys: tuple[str, ...], inside: Sequence[PairDecision],
         deterministic: Iterable[Sequence[str]], max_names: int) -> CutGroup | HeldBackGroup:
    """The smallest cut of a group holding a contradiction, or why it stays held back."""
    joining = [d for d in inside if d.merges]
    parts = connected([*deterministic, *((k,) for k in keys)])
    if len(parts) > max_names:
        return HeldBackGroup(keys, f"{len(parts)} names after deterministic merges, more than "
                                   f"the search bound of {max_names}", tuple(inside))
    try:
        cut = smallest_cut(parts, [(d.key_a, d.key_b) for d in joining],
                           [(d.key_a, d.key_b) for d in inside if d.answer in _AGAINST])
    except NoCut:
        return HeldBackGroup(keys, "no cut separates the contradiction", tuple(inside))
    pieces = tuple(tuple(sorted(p)) for p in cut.pieces)
    return CutGroup(
        keys=keys, pieces=pieces,
        kept=tuple(tuple(d for d in joining if {d.key_a, d.key_b} <= p) for p in cut.pieces),
        cut=tuple(d for d in joining if not any({d.key_a, d.key_b} <= p for p in cut.pieces)),
        tied=cut.tied, smallest_cuts=cut.smallest_cuts)


def _among(decisions: Iterable[PairDecision], keys: Iterable[str]) -> tuple[PairDecision, ...]:
    """The decisions between two of *keys*, by ID."""
    wanted = set(keys)
    return tuple(sorted((d for d in decisions if d.key_a in wanted and d.key_b in wanted),
                        key=lambda d: d.decision_id))


def _piece(keys: tuple[str, ...], inside: Sequence[PairDecision], cut: bool,
           max_names: int) -> DecisionGroup | HeldBackGroup | None:
    """A group or piece to merge, held back when too large; None when no `same` joins it."""
    joining = tuple(d for d in inside if d.merges)
    if not joining:
        return None
    if len(keys) > max_names:
        return HeldBackGroup(keys, f"piece has {len(keys)} names, more than {max_names}",
                             tuple(inside))
    return DecisionGroup(keys=keys, merging=joining, cut=cut)
