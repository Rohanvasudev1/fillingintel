"""The run report of `python -m resolve.run` (Step 9).

`benchmarks/resolution/run{N}.json` (committed): provenance, counts by label,
rule and pending, embedding calls and cost, every merge this run makes (new or
already logged), every pending pair with its scores, the `adjudication`
section (model settings, prompt, estimate, answers and reasons, tokens and
cost, held-back groups with every decision involved, related pairs and every
decision), and `labels_and_scoring`, which ticket 04 fills.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from graph.ontology import ONTOLOGY_VERSION
from resolve.candidates import ScoredPair
from resolve.config import ResolveConfig
from resolve.decisions import HeldBackGroup, PairDecision
from resolve.estimate import CostEstimate
from resolve.log import MergeEvent, Method, Stamp
from resolve.nodes import InputFile
from resolve.pipeline import Resolution
from resolve.prompt import ResolvePrompt
from resolve.request import (
    ADJUDICATION_PRICES,
    ADJUDICATOR_EFFORT,
    ADJUDICATOR_MAX_TOKENS,
    ADJUDICATOR_MODEL,
    ADJUDICATOR_PROVIDER,
    MAX_CHUNKS_PER_NODE,
)
from retrieve.answer_model import TokenUsage
from retrieve.pricing import anthropic_cost, embedding_cost, price_table


@dataclass(frozen=True, slots=True)
class EmbeddingUse:
    texts: int
    calls: int
    tokens: int


@dataclass(frozen=True, slots=True)
class AdjudicationInfo:
    """The adjudicator's prompt, the pre-run estimate and how many calls ran at a time."""

    prompt: ResolvePrompt
    estimate: CostEstimate
    workers: int


@dataclass(frozen=True, slots=True)
class RunInfo:
    """What the command knows about a run besides its result."""

    run: int
    stamp: Stamp
    config: ResolveConfig
    inputs: tuple[InputFile, ...]
    embedding: EmbeddingUse
    alias_map_keys: int
    adjudication: AdjudicationInfo | None = None
    complete: bool = True  # False when an adjudicator call failed; nothing was logged


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


def _decision(d: PairDecision) -> dict[str, object]:
    return {"decision_id": d.decision_id, "key_a": d.key_a, "key_b": d.key_b,
            "label": d.label, "answer": None if d.answer is None else d.answer.value,
            "related_kind": None if d.related_kind is None else d.related_kind.value,
            "quote_a": d.quote_a, "chunk_id_a": d.chunk_id_a, "quote_b": d.quote_b,
            "chunk_id_b": d.chunk_id_b, "reason": d.reason,
            "quotes_verified": d.quotes_verified, "failure": d.failure, "merges": d.merges,
            "from_cache": d.from_cache, "prompt_version": d.prompt_version}


def _held(group: HeldBackGroup) -> dict[str, object]:
    return {"keys": list(group.keys), "reason": group.reason,
            "decisions": [_decision(d) for d in group.decisions]}


def _usage(decisions: Sequence[PairDecision]) -> TokenUsage:
    used = [d.usage for d in decisions if d.usage is not None]
    return TokenUsage(
        input_tokens=sum(u.input_tokens for u in used),
        output_tokens=sum(u.output_tokens for u in used),
        cache_read_input_tokens=sum(u.cache_read_input_tokens for u in used),
        cache_creation_input_tokens=sum(u.cache_creation_input_tokens for u in used),
    )


def _cost(decisions: Sequence[PairDecision], *, cached: bool) -> float:
    return sum(anthropic_cost(ADJUDICATOR_MODEL, d.usage, ADJUDICATION_PRICES)
               for d in decisions if d.usage is not None and d.from_cache == cached)


def build_adjudication(result: Resolution, info: AdjudicationInfo) -> dict[str, object]:
    """The `adjudication` section: settings, prompt, estimate, outcomes, tokens and cost.

    Cost this run counts only calls made now; calls replayed from the response
    cache are priced separately, as what they cost when first made.
    """
    decisions = result.decisions
    usage = _usage([d for d in decisions if not d.from_cache])
    prompt, estimate = info.prompt, info.estimate
    return {
        "provider": ADJUDICATOR_PROVIDER, "model": ADJUDICATOR_MODEL,
        "effort": ADJUDICATOR_EFFORT, "max_tokens": ADJUDICATOR_MAX_TOKENS,
        "max_chunks_per_node": MAX_CHUNKS_PER_NODE, "workers": info.workers,
        "prompt_version": prompt.prompt_version, "prompt_sha256": prompt.sha256,
        "prompt_file_sha256": prompt.file_sha256, "prompt_path": prompt.path,
        "estimate": {"calls": estimate.calls, "input_tokens": estimate.input_tokens,
                     "output_tokens": estimate.output_tokens, "usd": round(estimate.usd, 4),
                     "price_table_date": estimate.price_table_date},
        "pairs": len(decisions),
        "calls_made": sum(d.usage is not None and not d.from_cache for d in decisions),
        "cache_hits": sum(d.from_cache for d in decisions),
        "answers": dict(result.counts.answers),
        "not_merged_by_reason": dict(result.counts.failures),
        "merging_decisions": sum(d.merges for d in decisions),
        "llm_merges": sum(m.method is Method.LLM for m in result.proposed),
        "held_back_groups": len(result.held_back),
        "tokens_this_run": {"input": usage.input_tokens, "output": usage.output_tokens,
                            "cache_read": usage.cache_read_input_tokens,
                            "cache_write": usage.cache_creation_input_tokens},
        "cost_this_run_usd": round(_cost(decisions, cached=False), 4),
        "cost_replayed_usd": round(_cost(decisions, cached=True), 4),
        "price_table": price_table([ADJUDICATOR_MODEL], ADJUDICATION_PRICES),
        "held_back": [_held(g) for g in result.held_back],
        "related": [_decision(d) for d in result.related],
        "decisions": [_decision(d) for d in decisions],
    }


def build_report(result: Resolution, info: RunInfo) -> dict[str, object]:
    counts = result.counts
    new_ids = {m.merge_id for m in result.merges}
    model = info.config.embedding_model
    return {
        "provenance": {
            "run": info.run, "status": "complete" if info.complete else "incomplete",
            "commit": info.stamp.commit, "created_at": info.stamp.timestamp,
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
        "adjudication": ({} if info.adjudication is None
                         else build_adjudication(result, info.adjudication)),
        "labels_and_scoring": {},  # ticket 04
    }


def write_report(path: Path, report: Mapping[str, object]) -> None:
    """Write the report to *path*, refusing to replace an existing run's report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

