"""The files an extraction run leaves: the candidates file and the run report (Step 8).

- Candidates, `data/extract/{accession_no}-run{N}.jsonl` (gitignored): one JSON
  line per merged node (`kind` node), merged edge (`kind` edge) and flag (`kind`
  flag), as `python -m extract.review` reads them.
- Run report, `benchmarks/extraction/{accession_no}-run{N}.json` (committed):
  provenance, counts, rejections by reason with examples, flags, conflicts,
  failed chunks, `check_batch()` violations, tokens and cost.

N is the next number not used by either file for that accession.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from extract.estimate import CostEstimate
from extract.outcomes import Rejection
from extract.pipeline import ExtractionRun
from extract.prompt import ExtractPrompt
from graph.batch import EdgeRecord, NodeRecord, NodeRef
from graph.ontology import ONTOLOGY_VERSION
from ingest.provenance import REPO_ROOT
from retrieve.answer_model import TokenUsage
from retrieve.pricing import PriceTable, anthropic_cost, price_table

CANDIDATES_DIR = REPO_ROOT / "data" / "extract"
REPORTS_DIR = REPO_ROOT / "benchmarks" / "extraction"
EXAMPLES_PER_REASON = 3


class RunStatus(StrEnum):
    COMPLETE = "complete"  # every chunk extracted and check_batch() found nothing
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class RunInfo:
    """What the command knows about a run besides its result."""

    run: int
    commit: str
    created_at: str
    model: str
    effort: str
    max_tokens: int
    prompt: ExtractPrompt
    workers: int
    estimate: CostEstimate
    prices: PriceTable
    candidates_file: str


def next_run_number(accession_no: str, *dirs: Path) -> int:
    """One more than the highest run number of *accession_no* in *dirs*, or 1."""
    pattern = re.compile(rf"{re.escape(accession_no)}-run(\d+)\.jsonl?")
    used = [int(m[1]) for d in dirs if d.is_dir() for p in d.iterdir()
            if (m := pattern.fullmatch(p.name))]
    return max(used, default=0) + 1


def candidates_path(directory: Path, accession_no: str, run: int) -> Path:
    return directory / f"{accession_no}-run{run}.jsonl"


def report_path(directory: Path, accession_no: str, run: int) -> Path:
    return directory / f"{accession_no}-run{run}.json"


# ── Candidates ────────────────────────────────────────────────────────────────

def _ref(ref: NodeRef) -> dict[str, object]:
    return {"label": ref.label, "key": ref.key_dict()}


def _node_line(node: NodeRecord) -> dict[str, object]:
    return {
        "kind": "node",
        "item": str(NodeRef.of(node.label, key=node.properties["key"])),  # as flags name it
        "label": node.label,
        "properties": dict(node.properties),
        "evidence": [{"chunk_id": e.chunk_id, "span": e.span, "confidence": e.confidence,
                      "extract_prompt": e.extract_prompt} for e in node.evidence],
    }


def _edge_line(edge: EdgeRecord) -> dict[str, object]:
    return {"kind": "edge", "item": f"{edge.start}-[{edge.type}]->{edge.end}",  # as flags name it
            "type": edge.type, "start": _ref(edge.start), "end": _ref(edge.end),
            "properties": dict(edge.properties)}


def candidate_lines(run: ExtractionRun) -> Iterator[dict[str, object]]:
    yield from (_node_line(n) for n in run.nodes)
    yield from (_edge_line(e) for e in run.edges)
    yield from ({"kind": "flag", "chunk_id": f.chunk_id, "reason": f.reason.value,
                 "item": f.item} for f in run.flags)


def write_candidates(path: Path, lines: Iterable[Mapping[str, object]]) -> None:
    """Write the JSON lines to *path*, refusing to replace an existing run's file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")


def write_report(path: Path, report: Mapping[str, object]) -> None:
    """Write the report to *path*, refusing to replace an existing run's report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


# ── Report ────────────────────────────────────────────────────────────────────

def _tokens(usage: TokenUsage) -> dict[str, int]:
    return {"input": usage.input_tokens, "cache_read": usage.cache_read_input_tokens,
            "cache_write": usage.cache_creation_input_tokens, "output": usage.output_tokens}


def _rejections(rejections: Sequence[Rejection], counts: Mapping[str, int],
                candidates: int) -> dict[str, object]:
    examples: dict[str, list[dict[str, object]]] = {}
    for r in rejections:
        shown = examples.setdefault(r.reason.value, [])
        if len(shown) < EXAMPLES_PER_REASON:
            shown.append({"chunk_id": r.chunk_id, "kind": r.kind.value, "message": r.message,
                          "candidate": dict(r.candidate)})
    return {"total": len(rejections),
            # rejected share of the candidate nodes and triples the model returned
            "rate": len(rejections) / candidates if candidates else 0.0,
            "by_reason": {str(reason): n for reason, n in sorted(counts.items())},
            "examples": examples}


def _costs(run: ExtractionRun, model: str, prices: PriceTable) -> tuple[float, float]:
    """The cost of every reply used, and of the calls this run made (cache misses)."""
    used = [c for c in run.chunks if c.usage is not None]
    total = sum(anthropic_cost(model, c.usage, prices) for c in used)
    fresh = sum(anthropic_cost(model, c.usage, prices) for c in used if not c.from_cache)
    return total, fresh


def build_report(run: ExtractionRun, info: RunInfo) -> dict[str, object]:
    model = info.model
    cost, fresh_cost = _costs(run, model, info.prices)
    candidates = sum(c.candidates for c in run.chunks)
    made = sum(1 for c in run.chunks if not c.from_cache and c.usage is not None)
    return {
        "accession_no": run.accession_no,
        "run": info.run,
        "status": (RunStatus.COMPLETE if run.complete else RunStatus.INCOMPLETE).value,
        "commit": info.commit,
        "created_at": info.created_at,
        "prompt": {"version": info.prompt.version, "path": info.prompt.path,
                   "sha256": info.prompt.sha256, "file_sha256": info.prompt.file_sha256,
                   "prompt_version": run.prompt_version},
        "model": model,
        "effort": info.effort,
        "max_tokens": info.max_tokens,
        "ontology_version": ONTOLOGY_VERSION,
        "workers": info.workers,
        "chunk_count": len(run.chunks),
        "candidates": candidates,
        "nodes": len(run.nodes),
        "edges": len(run.edges),
        "rejections": _rejections(run.rejections, run.rejection_counts, candidates),
        "flags": {str(reason): n for reason, n in sorted(run.flag_counts.items())},
        "conflicts": {"total": len(run.conflicts), "items": [
            {"item": c.item, "property": c.property, "kept": c.kept, "other": c.other,
             "kept_chunk": c.kept_chunk, "other_chunk": c.other_chunk} for c in run.conflicts]},
        "failed_chunks": [{"chunk_id": f.chunk_id, "reason": f.reason} for f in run.failures],
        "violations": [str(v) for v in run.violations],
        "calls": {"made": made, "from_cache": sum(1 for c in run.chunks if c.from_cache)},
        "tokens": _tokens(run.usage),
        "cost_usd": cost,
        "cost_this_run_usd": fresh_cost,
        "estimate": {"calls": info.estimate.calls, "usd": info.estimate.usd},
        "price_table": price_table([model], info.prices),
        "candidates_file": info.candidates_file,
    }

