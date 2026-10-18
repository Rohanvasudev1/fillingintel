"""The router's two routing maps, from predicted class to arm (Step 13).

The pre-registered map is RESEARCH-PLAN's H6 table, fixed before any run; it is
the headline router row.  The dev-best map is read from the file
``python -m eval.results_table --dev-best`` wrote once, never computed here, and
is always labelled "tuned on dev".  ``decline`` is in neither map: the router
declines those questions itself.  A map's SHA-256 covers its name and its
class-to-arm table, so the frozen record (``eval.freeze``) can pin both maps.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from ingest.provenance import REPO_ROOT

PRE_REGISTERED = "pre_registered"
DEV_BEST = "dev_best"
MAP_NAMES = (PRE_REGISTERED, DEV_BEST)
ROUTED_CLASSES = ("lookup", "local", "multi_hop", "global")
KNOWN_ARMS = ("vector", "graph_local", "graph_traversal", "graph_global")
DEV_BEST_FILE = REPO_ROOT / "benchmarks" / "router" / "dev-best-map.json"
H6_MAP: Mapping[str, str] = MappingProxyType({
    "lookup": "vector",
    "local": "graph_local",
    "multi_hop": "graph_traversal",
    "global": "graph_global",
})


class RoutingMapError(ValueError):
    """A routing map is unknown, missing or malformed; the message is safe to print."""


@dataclass(frozen=True)
class RoutingMap:
    name: str
    arms: Mapping[str, str]  # predicted class -> arm, in ROUTED_CLASSES order
    file: str | None  # repo-relative path of the dev-best map; None for the pre-registered map

    @property
    def sha256(self) -> str:
        canonical = json.dumps({"name": self.name, "map": dict(self.arms)}, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_map(name: str, dev_best_file: Path = DEV_BEST_FILE,
             known_arms: Collection[str] = KNOWN_ARMS) -> RoutingMap:
    """The routing map *name*; ``RoutingMapError`` when it cannot be used."""
    if name == PRE_REGISTERED:
        return RoutingMap(PRE_REGISTERED, H6_MAP, None)
    if name != DEV_BEST:
        raise RoutingMapError(f"unknown routing map {name!r}; choose from {', '.join(MAP_NAMES)}")
    try:
        document = json.loads(dev_best_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RoutingMapError(f"the dev-best map {dev_best_file.name} is not written yet "
                              "(python -m eval.results_table --dev-best)") from None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise RoutingMapError(f"cannot read the dev-best map {dev_best_file.name}: "
                              f"{type(exc).__name__}") from exc
    found = document.get("map") if isinstance(document, dict) else None
    if not isinstance(found, dict) or sorted(found) != sorted(ROUTED_CLASSES):
        raise RoutingMapError(f"the dev-best map {dev_best_file.name} must map exactly "
                              f"{', '.join(ROUTED_CLASSES)}")
    unknown = sorted({str(a) for a in found.values()} - set(known_arms))
    if unknown:
        raise RoutingMapError(f"the dev-best map {dev_best_file.name} names unknown arms "
                              f"{unknown}")
    return RoutingMap(DEV_BEST, MappingProxyType({c: found[c] for c in ROUTED_CLASSES}),
                      _display_path(dev_best_file))


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name
