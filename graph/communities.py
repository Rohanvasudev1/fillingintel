"""`python -m graph.communities`: find the communities and write their run file (Step 12).

Offline, no model calls. It reads what `graph.load` reads (Postgres, the
latest extract files, the alias map and the persistence links), runs the same
pure builder, turns its batch into the community graph
(graph/community_graph.py) and finds communities at two levels
(graph/community.py). The result goes to
`benchmarks/communities/communities-run{N}.json`: the commit, the settings and
their hash, the Leiden library version, the inputs' hashes, the community
graph's hash and size, counts and sizes per level, the singletons left out,
the unreportable communities and every community's members. Two runs over the
same inputs give the same file apart from the commit.

Usage: python -m graph.communities
Reads DATABASE_URL. Exit codes as graph.validate: 0 written; 2 bad arguments;
3 could not run (nothing written).
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections.abc import Callable, Sequence
from importlib.metadata import version
from pathlib import Path

from graph.builder import Built
from graph.community import (
    DEFAULT_SETTINGS,
    LOWER,
    TOP,
    Community,
    CommunityError,
    CommunitySettings,
    Level,
    find_communities,
)
from graph.community_graph import CommunityGraph, DuplicateKey, community_graph
from graph.load import LoadInputs
from graph.load_report import next_report_path
from graph.repo_build import CouldNotRun, read_built
from graph.validate import EXIT_OK, EXIT_RUN_ERROR
from ingest.atomic_json import write_json_atomic
from ingest.provenance import REPO_ROOT, git_state

__all__ = ["OUTPUT_DIR", "CouldNotRun", "communities_run", "main", "read_built"]

OUTPUT_DIR = REPO_ROOT / "benchmarks" / "communities"
RUN_PREFIX = "communities-run"
LIBRARY = "graspologic-native"


def _level(communities: Sequence[Community], level: Level) -> dict[str, object]:
    sizes = sorted((len(c.members) for c in communities if c.level == level), reverse=True)
    return {"count": len(sizes),
            "min": min(sizes, default=0), "median": statistics.median(sizes) if sizes else 0,
            "max": max(sizes, default=0), "sizes": sizes}


def communities_run(
    commit: str, inputs: LoadInputs, graph: CommunityGraph,
    communities: Sequence[Community], settings: CommunitySettings,
) -> dict[str, object]:
    """The run file's content: everything but the commit is the same on every run."""
    return {
        "commit": commit,
        "settings": settings.to_dict(),
        "settings_sha256": settings.sha256(),
        "library": {"name": LIBRARY, "version": version(LIBRARY)},
        "inputs": {
            "alias_map_sha256": inputs.alias_map_sha256,
            "persistence_links_sha256": inputs.links_sha256,
            "extract_files": {f.name: f.sha256 for f in inputs.files},
        },
        "community_graph": {
            "input_sha256": graph.input_sha256,
            "nodes": len(graph.labels),
            "pairs": len(graph.weights),
            "extracted_edge_pairs": len(graph.extracted),
            "co_occurrence_pairs": len(graph.co_occurrence),
        },
        "levels": {TOP: _level(communities, TOP), LOWER: _level(communities, LOWER)},
        "singletons": list(graph.singletons),
        "unreportable": [c.community_id for c in communities if not c.reportable],
        "communities": [
            {"community_id": c.community_id, "level": c.level, "parent": c.parent,
             "member_hash": c.member_hash, "reportable": c.reportable,
             "members": list(c.members)}
            for c in communities
        ],
    }


def main(
    argv: Sequence[str] | None = None, *, out_dir: Path = OUTPUT_DIR,
    read: Callable[[], tuple[LoadInputs, Built]] = read_built,
) -> int:
    """CLI entry point; exit codes in the module docstring."""
    argparse.ArgumentParser(
        description="Find Leiden communities at two levels over the community graph.",
    ).parse_args(argv)
    try:
        inputs, built = read()
        graph = community_graph(built.batch)
        communities = find_communities(graph, DEFAULT_SETTINGS)
    except (CouldNotRun, CommunityError, DuplicateKey) as exc:
        print(f"graph communities could not run; nothing was written: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = next_report_path(out_dir, RUN_PREFIX)
    report = communities_run(git_state(), inputs, graph, communities, DEFAULT_SETTINGS)
    write_json_atomic(path, report, indent=2)
    shown = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
    tops = sum(c.level == TOP for c in communities)
    print(f"{tops} top and {len(communities) - tops} lower communities over "
          f"{len(graph.labels)} nodes; {len(graph.singletons)} singletons left out. "
          f"Run file: {shown}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
