"""The report of one `python -m resolve.label` run (Step 9, ticket 04).

`benchmarks/resolution/{set}-label-run{N}.json` (committed): provenance (commit,
labeller model, effort and output cap, label prompt version and hashes, config
hash, ontology version, inputs), counts, failures by reason, tokens and cost.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime

from eval.judging.openai_backend import total_usage
from graph.ontology import ONTOLOGY_VERSION
from ingest.provenance import git_state
from resolve.config import ResolveConfig
from resolve.labeller import (
    LABEL_EFFORT,
    LABEL_MAX_OUTPUT_TOKENS,
    LABEL_MODEL,
    LABEL_NOTE,
    LABEL_PROVIDER,
    LabelOutcome,
    LabelPrompt,
)
from resolve.labels import PairLabel
from resolve.nodes import InputFile
from resolve.sample import Draw
from retrieve.pricing import price_table


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def build_label_report(drawn: Draw, labels: Sequence[PairLabel],
                       outcomes: Sequence[LabelOutcome], prompt: LabelPrompt,
                       inputs: Sequence[InputFile], config: ResolveConfig) -> dict[str, object]:
    failures = Counter(o.failure.split(" ", 1)[0] for o in outcomes if o.failure)
    usage = total_usage(o.usage for o in outcomes)
    return {
        "provenance": {
            "commit": git_state(), "created_at": now(), "note": LABEL_NOTE,
            "set": drawn.set_name,
            "labeller": {"provider": LABEL_PROVIDER, "model": LABEL_MODEL,
                         "effort": LABEL_EFFORT, "max_output_tokens": LABEL_MAX_OUTPUT_TOKENS},
            "label_prompt": {"version": prompt.prompt_version, "sha256": prompt.sha256,
                             "file_sha256": prompt.file_sha256},
            "config_hash": config.config_hash, "ontology_version": ONTOLOGY_VERSION,
            "inputs": [{"file": f.name, "sha256": f.sha256} for f in inputs],
        },
        "counts": {
            "drawn": len(drawn.pairs), "labelled": len(labels),
            "labelled_this_run": sum(o.label is not None for o in outcomes),
            "same": sum(label.same for label in labels),
            "quotes_unverified": sum(not label.quotes_verified for label in labels),
            "failures": dict(sorted(failures.items())),
            "replayed": sum(o.from_cache for o in outcomes),
        },
        "tokens": {"input": usage.input_tokens, "cached_input": usage.cached_input_tokens,
                   "output": usage.output_tokens, "reasoning": usage.reasoning_tokens},
        "cost_usd": round(math.fsum(o.cost_usd for o in outcomes), 6),
        "price_table": price_table([LABEL_MODEL]),
    }
