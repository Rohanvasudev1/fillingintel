"""The committed load report, `benchmarks/graph/load-run{N}.json` (Step 10).

It records what was loaded and from what: the commit, the alias map's and
every extract file's SHA-256, the ontology version, node counts per label and
edge counts per type (EVIDENCED_BY counted from node evidence), what the build
left out or found, the chunk gaps, write time and the validation result.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from graph.batch import EVIDENCED_BY, Batch
from graph.ontology import ONTOLOGY_VERSION

if TYPE_CHECKING:
    from graph.load import LoadInputs, LoadResult

_REPORT = re.compile(r"load-run(\d+)\.json")


def next_report_path(directory: Path) -> Path:
    """`load-run{N}.json` in *directory*, N one past the highest used (from 1)."""
    used = [int(m[1]) for p in directory.glob("load-run*.json") if (m := _REPORT.fullmatch(p.name))]
    return directory / f"load-run{max(used, default=0) + 1}.json"


def graph_counts(batch: Batch) -> dict[str, dict[str, int]]:
    """Nodes per label and edges per type that *batch* writes, sorted by name."""
    edges = Counter(e.type for e in batch.edges)
    edges[EVIDENCED_BY] = sum(len(n.evidence) for n in batch.nodes)
    return {"nodes": dict(sorted(Counter(n.label for n in batch.nodes).items())),
            "edges": dict(sorted(edges.items()))}


def load_report(commit: str, inputs: LoadInputs, result: LoadResult) -> dict[str, object]:
    build = result.built.report
    validation = result.validation
    return {
        "commit": commit,
        "ontology_version": ONTOLOGY_VERSION,
        "alias_map_sha256": inputs.alias_map_sha256,
        "extract_files": {f.name: f.sha256 for f in inputs.files},
        "counts": graph_counts(result.built.batch),
        "conflicts": [
            {"item": c.item, "property": c.property, "kept": c.kept, "other": c.other,
             "kept_chunk": c.kept_chunk, "other_chunk": c.other_chunk}
            for c in build.conflicts
        ],
        "skipped_metric_values": list(build.skipped_metric_values),
        "dropped_edges": [{"reason": d.reason.value, "edge": d.edge, "file": d.file}
                          for d in build.dropped_edges],
        "merged_into_company": list(build.merged_into_company),
        "chunk_gaps": [c for _, chunks in sorted(inputs.gaps.items()) for c in chunks],
        "write_seconds": round(result.write_seconds, 1),
        "validation": {
            "ok": validation.ok,
            "violations": validation.violation_count,
            "postgres_checked": validation.postgres_checked,
            "checks": {r.check.value: r.count for r in validation.results},
        },
    }
