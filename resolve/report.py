"""The run report of `python -m resolve.run` (Step 9).

`benchmarks/resolution/run{N}.json` (committed): provenance, counts by label,
rule and pending, embedding calls and cost, every merge this run makes (new or
already logged), every pending pair with its scores, and two sections later
tickets fill: `adjudication` (ticket 03) and `labels_and_scoring` (ticket 04).
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from graph.ontology import ONTOLOGY_VERSION
from resolve.candidates import ScoredPair
from resolve.config import ResolveConfig
from resolve.log import MergeEvent, Stamp
from resolve.nodes import InputFile
from resolve.pipeline import Resolution
from retrieve.pricing import embedding_cost, price_table


@dataclass(frozen=True, slots=True)
class EmbeddingUse:
    texts: int
    calls: int
    tokens: int


@dataclass(frozen=True, slots=True)
class RunInfo:
    """What the command knows about a run besides its result."""

    run: int
    stamp: Stamp
    config: ResolveConfig
    inputs: tuple[InputFile, ...]
    embedding: EmbeddingUse
    alias_map_keys: int


def _merge(merge: MergeEvent, new: bool) -> dict[str, object]:
    return {"merge_id": merge.merge_id, "new": new, "label": merge.label,
            "canonical": merge.canonical, "members": dict(merge.members),
            "method": merge.method.value, "rule": merge.rule}


def _pair(pair: ScoredPair) -> dict[str, object]:
    return {"key_a": pair.key_a, "key_b": pair.key_b, "label": pair.label,
            "rules": sorted(r.value for r in pair.rules), "fuzzy": round(pair.fuzzy, 2),
            "acronym": pair.acronym, "shared_rare_words": list(pair.shared_rare_words),
            "embedding_rank": pair.embedding_rank,
            "cosine": None if pair.cosine is None else round(pair.cosine, 4)}


def _rules(counts: Mapping[object, int]) -> dict[str, int]:
    return {str(rule): n for rule, n in counts.items()}


def build_report(result: Resolution, info: RunInfo) -> dict[str, object]:
    counts = result.counts
    new_ids = {m.merge_id for m in result.merges}
    model = info.config.embedding_model
    return {
        "provenance": {
            "run": info.run, "commit": info.stamp.commit, "created_at": info.stamp.timestamp,
            "config_hash": info.config.config_hash, "config": info.config.to_dict(),
            "ontology_version": ONTOLOGY_VERSION, "embedding_model": model,
            "embedding_input_type": info.config.embed_input_type,
            "inputs": [{"file": f.name, "sha256": f.sha256} for f in info.inputs],
        },
        "counts": {
            "nodes_by_label": dict(counts.nodes_by_label),
            "pairs_scored_by_label": dict(counts.pairs_by_label),
            "candidates_by_label": dict(counts.candidates_by_label),
            "candidates_by_rule": _rules(counts.candidates_by_rule),
            "candidates_by_rule_alone": _rules(counts.only_rule),
            "merges": {"proposed": len(result.proposed), "new": len(result.merges),
                       "by_rule": dict(counts.merges_by_rule)},
            "pending": len(result.pending),
            "pending_by_label": dict(counts.pending_by_label),
            "alias_map_keys": info.alias_map_keys,
        },
        "embedding": {
            "texts": info.embedding.texts, "calls": info.embedding.calls,
            "tokens": info.embedding.tokens,
            "cost_usd": embedding_cost(model, info.embedding.tokens),
            "price_table": price_table([model]),
        },
        "merges": [_merge(m, m.merge_id in new_ids) for m in result.proposed],
        "pending": [_pair(p) for p in result.pending],
        "adjudication": {},  # ticket 03
        "labels_and_scoring": {},  # ticket 04
    }


def write_report(path: Path, report: Mapping[str, object]) -> None:
    """Write the report to *path*, refusing to replace an existing run's report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

