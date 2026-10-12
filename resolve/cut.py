"""Group cut: the smallest cut of a held-back group into pieces (Step 9b).

A held-back group is a set of keys joined by `same` answers (cuttable) and
deterministic merges (fixed), holding some `related` or `distinct` pairs
(cannot-links). The keys a deterministic merge joins form one part, which is
never divided. A valid cut is a partition of the parts into pieces, each
connected by `same` answers, with no cannot-link pair inside a piece; its size
is the number of `same` answers between pieces. The smallest valid cuts are
found exactly, by a branch-and-bound search over every partition of the parts
(docs/research/er-cluster-splitting.md), so the result does not depend on the
order the answers are read.

When several cuts share the smallest size, two parts share a final piece only
if every tied cut places them together, and the final pieces are then split
into their connected parts. A part that ends up alone although some tied cut
gave it piece-mates is reported as left unmerged by the tie. When a
cannot-link pair lies inside one part, no cut separates it.

The search visits at most Bell(n) partitions of n parts, so the caller bounds
n (resolve.groups holds back a group with more parts than the name limit,
12; Bell(12) is about 4.2 million). The cannot-links and the size bound prune
most of them: 40 random 12-part groups took at most 3.3 s each.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from resolve.unionfind import connected

PairKeys = tuple[str, str]


@dataclass(frozen=True, slots=True)
class Cut:
    """The final pieces of a cut group, as sets of keys sorted by smallest key."""

    pieces: tuple[frozenset[str], ...]
    smallest_cuts: int  # how many cuts share the smallest size
    tied: tuple[str, ...]  # keys left alone that some tied cut would have merged, sorted


class NoCut(ValueError):
    """A cannot-link pair lies inside one part, so no cut separates it."""


def smallest_cut(parts: Sequence[frozenset[str]], same: Sequence[PairKeys],
                 cannot: Collection[PairKeys]) -> Cut:
    """Cut *parts* by removing the fewest *same* links so no *cannot* pair shares a piece.

    *same* may name a pair more than once; each counts as one answer to cut.
    """
    ordered = sorted(parts, key=min)
    index = {key: i for i, part in enumerate(ordered) for key in part}
    weights: dict[PairKeys, int] = {}
    for a, b in same:
        i, j = sorted((index[a], index[b]))
        if i != j:
            weights[(i, j)] = weights.get((i, j), 0) + 1
    apart: set[tuple[int, int]] = set()
    for a, b in cannot:
        i, j = sorted((index[a], index[b]))
        if i == j:
            raise NoCut(f"{a} and {b} are joined by a deterministic merge")
        apart.add((i, j))
    best = _Search(len(ordered), weights, apart).run()
    final = _meet(best, weights)
    pieces = tuple(sorted((frozenset().union(*(ordered[i] for i in piece)) for piece in final),
                          key=min))
    tied = sorted(k for i, part in enumerate(ordered) for k in part
                  if len(_block_of(i, final)) == 1 and any(len(_block_of(i, p)) > 1 for p in best))
    return Cut(pieces=pieces, smallest_cuts=len(best), tied=tuple(tied))


class _Search:
    """Branch and bound over partitions of parts 0..n-1, keeping every smallest valid one."""

    def __init__(self, n: int, weights: Mapping[tuple[int, int], int],
                 apart: Collection[tuple[int, int]]):
        self.n = n
        self.weights = weights
        self.links = [[weights.get((min(i, j), max(i, j)), 0) for j in range(n)]
                      for i in range(n)]
        self.apart = [{j for a, b in apart for i2, j in ((a, b), (b, a)) if i2 == i}
                      for i in range(n)]
        self.best_size = sum(weights.values())  # every part alone is always valid
        self.best: list[tuple[tuple[int, ...], ...]] = []

    def run(self) -> list[tuple[tuple[int, ...], ...]]:
        self._place(0, [], 0)
        return self.best

    def _place(self, i: int, blocks: list[list[int]], size: int) -> None:
        if size > self.best_size:
            return
        if i == self.n:
            self._keep(blocks, size)
            return
        before = sum(self.links[i][j] for j in range(i))
        for block in blocks:
            if self.apart[i].isdisjoint(block):
                block.append(i)
                self._place(i + 1, blocks, size + before - sum(self.links[i][j] for j in block))
                block.pop()
        blocks.append([i])
        self._place(i + 1, blocks, size + before)
        blocks.pop()

    def _keep(self, blocks: Sequence[Sequence[int]], size: int) -> None:
        if not all(self._connected(b) for b in blocks):
            return  # its connected parts make the same cut, and are visited too
        if size < self.best_size or not self.best:
            self.best_size, self.best = size, []
        self.best.append(tuple(tuple(b) for b in blocks))

    def _connected(self, block: Sequence[int]) -> bool:
        return len(connected([(i, j) for i in block for j in block
                              if i < j and self.links[i][j]] + [(i,) for i in block])) == 1


def _meet(partitions: Sequence[Sequence[Sequence[int]]],
          weights: Mapping[tuple[int, int], int]) -> list[tuple[int, ...]]:
    """Parts together in every partition, split into pieces connected by `same` links."""
    signature: dict[tuple[int, ...], list[int]] = {}
    parts = sorted(i for block in partitions[0] for i in block)
    for i in parts:
        signature.setdefault(tuple(_block_of(i, p) for p in partitions), []).append(i)
    pieces: list[tuple[int, ...]] = []
    for together in signature.values():
        inside = set(together)
        links = [(i, j) for (i, j) in weights if i in inside and j in inside]
        pieces += [tuple(sorted(g)) for g in connected([*links, *((i,) for i in together)])]
    return sorted(pieces)


def _block_of(i: int, partition: Sequence[Sequence[int]]) -> tuple[int, ...]:
    return next(tuple(sorted(b)) for b in partition if i in b)
