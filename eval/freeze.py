"""The frozen record ``test`` is scored against: ``python -m eval.freeze`` (Step 13).

``benchmarks/router/frozen.json`` pins the router's prompt version and hash and
its effort, both routing maps' SHA-256, and each arm's config hash and prompt
versions.  An arm's config hash is the SHA-256 of its provenance as its run
header records it (``eval.router_report.arm_provenance``): models, efforts,
answer prompt, k, chunker version and, for a graph arm, the graph load run,
ontology, schema hash and its Cypher, map and reduce prompts.  The command
opens every arm to read its config (it needs the databases and the API keys
set, but makes no model call), refuses a dirty tree, and writes the file once.

``eval.run --final`` refuses when the record is missing, and when the arm it
opens, or the router and any arm the router opens, differs from it.

Exit codes: 0 written; 2 the tree is dirty or the record exists; 3 could not run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from eval.router_report import arm_provenance
from ingest.provenance import REPO_ROOT, git_state
from retrieve.arm import ArmConfig, ArmError, ArmSpec
from retrieve.arms import BASE_ARMS
from retrieve.response_cache import DEFAULT_CACHE_DIR
from retrieve.route_classifier import ROUTE_EFFORT, ROUTE_MODEL, ROUTE_PROMPT_VERSION
from retrieve.route_prompt import HASH_CHARS, RoutePrompt, RoutePromptError, load_route_prompt
from retrieve.routing_maps import MAP_NAMES, RoutingMap, RoutingMapError, load_map

FROZEN_FILE = REPO_ROOT / "benchmarks" / "router" / "frozen.json"
LABEL = ("frozen before the test split is scored; eval.run --final refuses any arm, router "
         "setting or map that differs")
EXIT_OK, EXIT_USAGE, EXIT_COULD_NOT_RUN = 0, 2, 3
DIRTY = "+dirty"


class FreezeError(ValueError):
    """The frozen record is missing, unreadable, or does not match; safe to print."""


def arm_config_sha256(config: ArmConfig) -> str:
    canonical = json.dumps(arm_provenance(config), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prompt_versions(config: ArmConfig) -> list[str]:
    """Every prompt version an arm's config names, sorted."""
    answer = config.answer_prompt
    # graph_global's answer prompt is its reduce prompt, whose version is already full.
    found = {answer.version if "/" in answer.version
             else f"answer/{answer.version}@{answer.sha256[:HASH_CHARS]}"}
    graph = config.graph
    if graph is not None:
        if graph.cypher_prompt_version is not None:
            found.add(graph.cypher_prompt_version)
        if graph.global_search is not None:
            found |= {graph.global_search.map_prompt_version,
                      graph.global_search.reduce_prompt_version}
    return sorted(found)


def frozen_document(*, commit: str, router_prompt: RoutePrompt, router_effort: str,
                    maps: Sequence[RoutingMap],
                    arm_configs: Mapping[str, ArmConfig]) -> dict[str, object]:
    return {
        "label": LABEL,
        "commit": commit,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "router": {"model": ROUTE_MODEL, "effort": router_effort,
                   "prompt_version": router_prompt.prompt_version,
                   "prompt_sha256": router_prompt.sha256},
        "maps": {m.name: {"arms": dict(m.arms), "sha256": m.sha256, "file": m.file}
                 for m in maps},
        "arms": {name: {"config_sha256": arm_config_sha256(c),
                        "prompt_versions": prompt_versions(c)}
                 for name, c in arm_configs.items()},
    }


def write_frozen(document: Mapping[str, object], path: Path = FROZEN_FILE) -> None:
    """Write the record once; ``FreezeError`` when it exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8") as fh:
            json.dump(document, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
    except FileExistsError as exc:
        raise FreezeError(f"{path.name} already exists; the record is written once") from exc


def read_frozen(path: Path = FROZEN_FILE) -> Mapping[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FreezeError(f"no frozen record at {path.name}; --final needs one "
                          "(python -m eval.freeze)") from None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise FreezeError(f"cannot read the frozen record {path.name}: "
                          f"{type(exc).__name__}") from exc
    if not (isinstance(document, dict) and isinstance(document.get("arms"), dict)
            and isinstance(document.get("router"), dict)
            and isinstance(document.get("maps"), dict)):
        raise FreezeError(f"the frozen record {path.name} lacks its router, maps or arms")
    return document


def config_problem(frozen: Mapping[str, object], name: str, config: ArmConfig) -> str | None:
    """Why *config* (arm *name*'s, or the router's) differs from *frozen*, or None."""
    router = config.router
    if router is None:
        return _arm_problem(frozen, name, config)
    live = {"model": router.model, "effort": router.effort,
            "prompt_version": router.prompt_version, "prompt_sha256": router.prompt_sha256}
    if {k: frozen["router"].get(k) for k in live} != live:
        return "the router differs from the frozen record (model, effort or prompt)"
    pinned = frozen["maps"].get(router.map_name)
    if not isinstance(pinned, dict) or pinned.get("sha256") != router.map_sha256:
        return f"the router's {router.map_name} map differs from the frozen record"
    for arm, inner in router.arms.items():
        problem = _arm_problem(frozen, arm, inner)
        if problem is not None:
            return problem
    return None


def _arm_problem(frozen: Mapping[str, object], name: str, config: ArmConfig) -> str | None:
    pinned = frozen["arms"].get(name)
    if not isinstance(pinned, dict) or pinned.get("config_sha256") != arm_config_sha256(config):
        return (f"{name} differs from the frozen record (its config, prompts or graph changed "
                "since the freeze)")
    return None


def _configs(specs: Iterable[ArmSpec], response_cache: Path) -> dict[str, ArmConfig]:
    with ExitStack() as stack:
        return {s.name: stack.enter_context(s.open(response_cache)).config for s in specs}


def main(argv: Sequence[str] | None = None, *, path: Path = FROZEN_FILE) -> int:
    parser = argparse.ArgumentParser(description="Write the frozen record test is scored on.")
    parser.parse_args(argv)
    commit = git_state()
    if commit.endswith(DIRTY):
        print(f"the tree is dirty ({commit}); commit first, then freeze", file=sys.stderr)
        return EXIT_USAGE
    if path.exists():
        print(f"{path.name} already exists; the record is written once", file=sys.stderr)
        return EXIT_USAGE
    try:
        maps = [load_map(name, BASE_ARMS) for name in MAP_NAMES]
        prompt = load_route_prompt(ROUTE_PROMPT_VERSION)
        configs = _configs(BASE_ARMS.values(), DEFAULT_CACHE_DIR)
        write_frozen(frozen_document(commit=commit, router_prompt=prompt,
                                     router_effort=ROUTE_EFFORT, maps=maps,
                                     arm_configs=configs), path)
    except FreezeError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    except (RoutingMapError, RoutePromptError, ArmError) as exc:
        print(f"could not freeze: {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    print(f"wrote {path} at {commit}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
