"""The run report of `python -m persist.run` (Step 10).

`benchmarks/persistence/run{N}.json` (committed): provenance (commit, config
and hash, prompt, model settings, inputs, the link file's hash), counts (filing
pairs, risk factors, candidates, answers, reasons not linked, links by method,
per filing pair), embedding use, the adjudication section (estimate, calls,
tokens and cost), the check_batch() dry run, and every decision.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from graph.batch import Violation
from graph.ontology import ONTOLOGY_VERSION
from persist.config import PersistConfig
from persist.decisions import PersistDecision
from persist.pipeline import Linking
from persist.prompt import PersistPrompt
from persist.request import (
    ADJUDICATION_PRICES,
    ADJUDICATOR_EFFORT,
    ADJUDICATOR_MAX_TOKENS,
    ADJUDICATOR_MODEL,
    ADJUDICATOR_PROVIDER,
    MAX_CHUNKS_PER_FACTOR,
)
from resolve.estimate import CostEstimate
from resolve.nodes import InputFile
from retrieve.answer_model import TokenUsage
from retrieve.pricing import anthropic_cost, embedding_cost, price_table

_EXAMPLES = 5


@dataclass(frozen=True, slots=True)
class EmbeddingUse:
    texts: int
    calls: int
    tokens: int


@dataclass(frozen=True, slots=True)
class RunInfo:
    """What the command knows about a run besides its result."""

    run: int
    commit: str
    created_at: str
    config: PersistConfig
    prompt: PersistPrompt
    inputs: tuple[InputFile, ...]
    filings: int
    embedding: EmbeddingUse
    estimate: CostEstimate
    workers: int
    links_sha256: str | None  # None when the link file was not written
    complete: bool


def _usage(decisions: Sequence[PersistDecision]) -> TokenUsage:
    used = [d.usage for d in decisions if d.usage is not None]
    return TokenUsage(
        input_tokens=sum(u.input_tokens for u in used),
        output_tokens=sum(u.output_tokens for u in used),
        cache_read_input_tokens=sum(u.cache_read_input_tokens for u in used),
        cache_creation_input_tokens=sum(u.cache_creation_input_tokens for u in used),
    )


def _cost(decisions: Sequence[PersistDecision], *, cached: bool) -> float:
    return sum(anthropic_cost(ADJUDICATOR_MODEL, d.usage, ADJUDICATION_PRICES)
               for d in decisions if d.usage is not None and d.from_cache == cached)


def _decision(d: PersistDecision) -> dict[str, object]:
    return {"decision_id": d.decision_id, "later_key": d.later_key,
            "earlier_key": d.earlier_key, "rank": d.rank,
            "answer": None if d.answer is None else d.answer.value,
            "quote_earlier": d.quote_earlier, "chunk_id_earlier": d.chunk_id_earlier,
            "quote_later": d.quote_later, "chunk_id_later": d.chunk_id_later,
            "reason": d.reason, "failure": d.failure, "links": d.links,
            "from_cache": d.from_cache, "prompt_version": d.prompt_version}


def _adjudication(decisions: Sequence[PersistDecision], info: RunInfo) -> dict[str, object]:
    """Cost this run counts only calls made now; replayed calls are priced separately."""
    usage = _usage([d for d in decisions if not d.from_cache])
    estimate = info.estimate
    return {
        "workers": info.workers,
        "estimate": {"calls": estimate.calls, "input_tokens": estimate.input_tokens,
                     "output_tokens": estimate.output_tokens, "usd": round(estimate.usd, 4),
                     "price_table_date": estimate.price_table_date},
        "calls_made": sum(d.usage is not None and not d.from_cache for d in decisions),
        "cache_hits": sum(d.from_cache for d in decisions),
        "tokens_this_run": {"input": usage.input_tokens, "output": usage.output_tokens,
                            "cache_read": usage.cache_read_input_tokens,
                            "cache_write": usage.cache_creation_input_tokens},
        "cost_this_run_usd": round(_cost(decisions, cached=False), 4),
        "cost_replayed_usd": round(_cost(decisions, cached=True), 4),
        "price_table": price_table([ADJUDICATOR_MODEL], ADJUDICATION_PRICES),
    }


def build_report(result: Linking, violations: Sequence[Violation],
                 info: RunInfo) -> dict[str, object]:
    decisions = result.decisions
    prompt, model = info.prompt, info.config.embedding_model
    return {
        "provenance": {
            "run": info.run, "status": "complete" if info.complete else "incomplete",
            "commit": info.commit, "created_at": info.created_at,
            "config_hash": info.config.config_hash, "config": info.config.to_dict(),
            "ontology_version": ONTOLOGY_VERSION,
            "provider": ADJUDICATOR_PROVIDER, "model": ADJUDICATOR_MODEL,
            "effort": ADJUDICATOR_EFFORT, "max_tokens": ADJUDICATOR_MAX_TOKENS,
            "max_chunks_per_factor": MAX_CHUNKS_PER_FACTOR,
            "prompt_version": prompt.prompt_version, "prompt_sha256": prompt.sha256,
            "prompt_file_sha256": prompt.file_sha256, "prompt_path": prompt.path,
            "inputs": [{"file": f.name, "sha256": f.sha256} for f in info.inputs],
            "filings": info.filings,
            "links_sha256": info.links_sha256,
        },
        "counts": {
            "filing_pairs": len(result.pairs),
            "later_factors": sum(c.later_factors for c in result.pair_counts),
            "exact_title_matches": len(result.exact),
            "candidates": len(result.candidates),
            "answers": dict(sorted(Counter(d.answer.value if d.answer else "none"
                                           for d in decisions).items())),
            "not_linked_by_reason": dict(sorted(Counter(d.failure for d in decisions
                                                        if d.failure).items())),
            "links": len(result.links),
            "links_by_method": dict(sorted(Counter(lk.method for lk in result.links).items())),
            "by_filing_pair": [asdict(c) for c in result.pair_counts],
        },
        "embedding": {
            "factors": result.embedded, "texts": info.embedding.texts,
            "calls": info.embedding.calls, "tokens": info.embedding.tokens,
            "cost_usd": embedding_cost(model, info.embedding.tokens),
            "price_table": price_table([model]),
        },
        "adjudication": _adjudication(decisions, info),
        "check_batch": {"violations": len(violations),
                        "examples": [str(v) for v in violations[:_EXAMPLES]]},
        "decisions": [_decision(d) for d in decisions],
    }


def write_report(path: Path, report: Mapping[str, object]) -> None:
    """Write the report to *path*, refusing to replace an existing run's report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
