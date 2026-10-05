"""Retrieval metrics computed from chunk IDs, with no LLM (Step 5).

recall@k    = gold chunks in the top k / gold chunks
precision@k = gold chunks in the top k / k

Precision divides by k even when fewer than k chunks came back, so an arm
cannot raise its precision by returning less.  Records without gold chunks
(decline, unanswerable) have no retrieval score; asking for one is an error.
"""
from __future__ import annotations

from collections.abc import Sequence

METRIC_KS = (5, 10)
DEFINITIONS = {
    "recall@k": "gold chunks in the top k / gold chunks",
    "precision@k": "gold chunks in the top k / k, even when fewer than k come back",
}


def _hits(retrieved: Sequence[str], gold: Sequence[str], k: int) -> int:
    if k < 1:
        raise ValueError(f"k must be at least 1, got {k}")
    if not gold:
        raise ValueError("a record with no gold chunks has no retrieval score")
    return len(set(retrieved[:k]) & set(gold))


def recall_at_k(retrieved: Sequence[str], gold: Sequence[str], k: int) -> float:
    """Share of *gold* chunk IDs found in the first *k* of *retrieved*."""
    return _hits(retrieved, gold, k) / len(set(gold))


def precision_at_k(retrieved: Sequence[str], gold: Sequence[str], k: int) -> float:
    """Gold chunk IDs in the first *k* of *retrieved*, over *k*."""
    return _hits(retrieved, gold, k) / k
