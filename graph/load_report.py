"""The committed load report, `benchmarks/graph/load-run{N}.json` (Step 10).

It records what was loaded and from what: the commit, the SHA-256 of the alias
map, the link file and every extract file, PERSISTS_AS links by method, the
ontology version (and the older one :GraphMeta was upgraded from, if any), the
extract prompts and the ontology versions the evidence was extracted under,
node counts per label and edge counts per type (EVIDENCED_BY counted from node
evidence), what the build left out or found, risk factor body counts, the
communities and reports files with community counts (Step 12), the chunk gaps,
write time and the validation result.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from graph.batch import EVIDENCED_BY, Batch
from graph.bodies import BODY_PROMPT, LONG_BODY_CHUNKS
from graph.ontology import ONTOLOGY_VERSION

if TYPE_CHECKING:
    from graph.load import LoadInputs, LoadResult

LOAD_RUN = "load-run"


def _used_runs(directory: Path, prefix: str) -> list[int]:
    pattern = re.compile(rf"{re.escape(prefix)}(\d+)\.json")
    return [int(m[1]) for p in directory.glob(f"{prefix}*.json")
            if (m := pattern.fullmatch(p.name))]


def next_report_path(directory: Path, prefix: str = LOAD_RUN) -> Path:
    """`{prefix}{N}.json` in *directory*, N one past the highest used (from 1)."""
    return directory / f"{prefix}{max(_used_runs(directory, prefix), default=0) + 1}.json"


def latest_load_run(directory: Path) -> str | None:
    """The newest load report's run name in *directory*, such as "load-run4", or None."""
    used = _used_runs(directory, LOAD_RUN)
    return f"{LOAD_RUN}{max(used)}" if used else None


def graph_counts(batch: Batch) -> dict[str, dict[str, int]]:
    """Nodes per label and edges per type that *batch* writes, sorted by name."""
    edges = Counter(e.type for e in batch.edges)
    edges[EVIDENCED_BY] = sum(len(n.evidence) for n in batch.nodes)
    return {"nodes": dict(sorted(Counter(n.label for n in batch.nodes).items())),
            "edges": dict(sorted(edges.items()))}


def load_report(commit: str, inputs: LoadInputs, result: LoadResult) -> dict[str, object]:
    build = result.built.report
    return {
        "commit": commit,
        "ontology_version": ONTOLOGY_VERSION,
        "graph_meta_upgraded_from": result.upgraded_from,
        "evidence_provenance": {
            "extract_prompts": sorted({p for f in inputs.files for p in f.prompts}),
            "extracted_under_ontology": list(inputs.extracted_under),
        },
        "alias_map_sha256": inputs.alias_map_sha256,
        "extract_files": {f.name: f.sha256 for f in inputs.files},
        "persistence_links": {
            "sha256": inputs.links_sha256,
            "by_method": dict(sorted(Counter(link.method for link in inputs.links).items())),
        },
        "counts": graph_counts(result.batch),
        "conflicts": [c.to_dict() for c in build.conflicts],
        "skipped_metric_values": list(build.skipped_metric_values),
        "dropped_edges": [{"reason": d.reason.value, "edge": d.edge, "file": d.file}
                          for d in build.dropped_edges],
        "merged_into_company": list(build.merged_into_company),
        "risk_factor_bodies": {
            "prompt": BODY_PROMPT,
            "body_entries": build.bodies.body_entries,
            "unlocated": len(build.bodies.unlocated),
            "not_headings": len(build.bodies.not_headings),
            "long_body_threshold_chunks": LONG_BODY_CHUNKS,
            "long_bodies": [{"key": key, "chunks": n} for key, n in build.bodies.long_bodies],
            "unlocated_keys": list(build.bodies.unlocated),
            "not_heading_keys": list(build.bodies.not_headings),
        },
        "communities": _communities(result),
        "chunk_gaps": [c for _, chunks in sorted(inputs.gaps.items()) for c in chunks],
        "write_seconds": round(result.write_seconds, 1),
        "validation": _validation(result),
    }


def _communities(result: LoadResult) -> dict[str, object] | None:
    records = result.communities
    return None if records is None else {**records.sources, "counts": dict(records.counts)}


def _validation(result: LoadResult) -> dict[str, object]:
    report = result.validation
    if report is None:
        return {"ran": False, "error": result.validation_error}
    return {"ran": True, "ok": report.ok, "violations": report.violation_count,
            "postgres_checked": report.postgres_checked,
            "checks": {r.check.value: r.count for r in report.results}}
