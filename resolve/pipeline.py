"""resolve_nodes(): one resolution pass over extracted nodes (Step 9).

1. Named nodes are embedded (when the config's top_k is above 0).
2. Every pair the rules can see is scored (resolve.candidates).
3. Deterministic merges: names equal after normalization within a label, and
   an Organization in the filer-name table into its Company. Groups are joined
   by shared keys; each group is one `merge` event with method `deterministic`.
4. Every other candidate pair goes to the adjudicator. Until ticket 03 wires
   one in, they are returned as pending and never merged.

The function reads nothing and writes nothing; the caller passes the merge log
already on disk, and only merges whose IDs it lacks come back as new.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from resolve.candidates import Rule, ScoredPair, is_named, score_pairs
from resolve.config import ResolveConfig
from resolve.decisions import Adjudicator, HeldBackGroup, PairDecision
from resolve.embed import NodeEmbedder, embed_text
from resolve.filers import FILER_RULE, FilerEntry, filer_entry
from resolve.groups import COMPANY_PREFIX, connected
from resolve.log import EvidenceQuote, LogEvent, MergeEvent, Method, Stamp, merge_event, new_merges
from resolve.nodes import ResolveNode


@dataclass(frozen=True)
class ResolutionCounts:
    nodes_by_label: Mapping[str, int]
    pairs_by_label: Mapping[str, int]
    candidates_by_label: Mapping[str, int]
    candidates_by_rule: Mapping[Rule, int]
    only_rule: Mapping[Rule, int]  # candidate pairs this rule alone produced
    merges_by_rule: Mapping[str, int]
    pending_by_label: Mapping[str, int]
    answers: Mapping[str, int] = field(default_factory=dict)  # ticket 03
    failures: Mapping[str, int] = field(default_factory=dict)  # ticket 03


@dataclass(frozen=True)
class Resolution:
    """What one pass decided. `merges` holds only events the log lacks."""

    merges: tuple[MergeEvent, ...]
    proposed: tuple[MergeEvent, ...]  # every merge this pass makes, logged before or not
    pairs: tuple[ScoredPair, ...]
    pending: tuple[ScoredPair, ...]  # candidate pairs awaiting the adjudicator
    counts: ResolutionCounts
    decisions: tuple[PairDecision, ...] = ()
    held_back: tuple[HeldBackGroup, ...] = ()
    related: tuple[PairDecision, ...] = ()


def resolve_nodes(nodes: Sequence[ResolveNode], embedder: NodeEmbedder | None,
                  adjudicator: Adjudicator | None, config: ResolveConfig, *,
                  log: Sequence[LogEvent] = (), stamp: Stamp) -> Resolution:
    """Resolve *nodes* (one per key) under *config*; see the module docstring."""
    if adjudicator is not None:
        raise NotImplementedError("the adjudicator is wired in by ticket 03")
    keys = Counter(n.key for n in nodes)
    if dupes := [k for k, n in keys.items() if n > 1]:
        raise ValueError(f"{len(dupes)} keys appear twice, such as {dupes[0]}")
    pairs = score_pairs(nodes, _vectors(nodes, embedder, config), config)
    proposed = _deterministic(nodes, pairs, stamp)
    grouped = {key: m.merge_id for m in proposed for key in m.members}
    pending = tuple(p for p in pairs if p.is_candidate
                    and (p.key_a not in grouped or grouped[p.key_a] != grouped.get(p.key_b)))
    return Resolution(merges=new_merges(log, proposed), proposed=tuple(proposed), pairs=pairs,
                      pending=pending, counts=_counts(nodes, pairs, proposed, pending))


def _vectors(nodes: Sequence[ResolveNode], embedder: NodeEmbedder | None,
             config: ResolveConfig) -> dict[str, Sequence[float]]:
    named = [n for n in nodes if is_named(n.label)]
    if not config.top_k or not named:
        return {}
    if embedder is None:
        raise ValueError("top_k is above 0, so an embedder is needed")
    vectors = embedder.embed([embed_text(n, config.embed_text) for n in named])
    if len(vectors) != len(named):
        raise ValueError(f"the embedder returned {len(vectors)} vectors for {len(named)} texts")
    return {n.key: v for n, v in zip(named, vectors, strict=True)}


def _deterministic(nodes: Sequence[ResolveNode], pairs: Sequence[ScoredPair],
                   stamp: Stamp) -> list[MergeEvent]:
    by_key = {n.key: n for n in nodes}
    links: list[tuple[str, str]] = [(p.key_a, p.key_b) for p in pairs
                                    if Rule.NORMALIZED_EQUAL in p.rules]
    entries: dict[str, FilerEntry] = {}
    for n in nodes:
        if (entry := filer_entry(n.label, n.name)) is not None:
            links.append((n.key, entry.company_key))
            entries[n.key] = entry
    return [_group_merge(group, by_key, entries, stamp) for group in connected(links)]


def _group_merge(group: frozenset[str], by_key: Mapping[str, ResolveNode],
                 entries: Mapping[str, FilerEntry], stamp: Stamp) -> MergeEvent:
    members = sorted(k for k in group if not k.startswith(COMPANY_PREFIX))
    evidence = [EvidenceQuote(k, by_key[k].name, by_key[k].evidence[0].chunk_id,
                              by_key[k].evidence[0].span) for k in members]
    table = sorted({entries[k] for k in members if k in entries},
                   key=lambda e: (e.company_key, e.name))
    evidence += [EvidenceQuote(e.company_key, e.name, None,
                               f"filer table: {e.name} is {e.ticker}, CIK {e.cik}")
                 for e in table]
    counts = {k: len(by_key[k].chunk_ids) for k in members}
    counts.update({e.company_key: 0 for e in table})
    rule = FILER_RULE if table else Rule.NORMALIZED_EQUAL.value
    return merge_event(label=by_key[members[0]].label, members=counts,
                       method=Method.DETERMINISTIC, rule=rule, evidence=evidence, stamp=stamp)


def _counts(nodes: Sequence[ResolveNode], pairs: Sequence[ScoredPair],
            proposed: Sequence[MergeEvent], pending: Sequence[ScoredPair]) -> ResolutionCounts:
    candidates = [p for p in pairs if p.is_candidate]
    return ResolutionCounts(
        nodes_by_label=dict(sorted(Counter(n.label for n in nodes).items())),
        pairs_by_label=dict(sorted(Counter(p.label for p in pairs).items())),
        candidates_by_label=dict(sorted(Counter(p.label for p in candidates).items())),
        candidates_by_rule={r: sum(r in p.rules for p in candidates) for r in Rule},
        only_rule={r: sum(p.rules == {r} for p in candidates) for r in Rule},
        merges_by_rule=dict(sorted(Counter(m.rule or m.method.value for m in proposed).items())),
        pending_by_label=dict(sorted(Counter(p.label for p in pending).items())),
    )
