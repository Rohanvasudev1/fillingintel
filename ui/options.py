"""The six picker options the Ask view offers (Step 14).

Each option is an arm, or the router under one routing map, with the committed
``dev`` run files its sample metrics and cost estimate are read from.  The
routers' runs answered every ``dev`` question; ``graph_global``'s full run
answered the global, decline and unanswerable ones, and its retrieval-only run
scored the rest.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from eval.results_inputs import DEV_ROUTER_RUNS, DEV_RUNS
from retrieve.arm import ArmSpec
from retrieve.routing_maps import DEV_BEST, PRE_REGISTERED
from ui.errors import UnknownOption


@dataclass(frozen=True)
class PickerOption:
    label: str  # what the picker shows, and the key everywhere else
    arm: str  # the registry name: an arm, or "router"
    map_name: str | None  # the router's routing map; None for an arm
    note: str | None  # shown beside the label
    dev_runs: tuple[str, ...]  # committed run files under benchmarks/runs, full run first


def _arm(name: str, note: str | None = None) -> PickerOption:
    return PickerOption(name, name, None, note, DEV_RUNS[name])


def _router(map_name: str, label: str, note: str | None) -> PickerOption:
    return PickerOption(label, "router", map_name, note, DEV_ROUTER_RUNS[map_name])


DEFAULT_OPTION = "vector"
OPTIONS: tuple[PickerOption, ...] = (
    _arm("vector", "best measured arm"),
    _router(DEV_BEST, "router (dev-best)", "tuned on dev"),
    _router(PRE_REGISTERED, "router (pre-registered)", None),
    _arm("graph_local"),
    _arm("graph_traversal"),
    _arm("graph_global"),
)
OPTIONS_BY_LABEL: Mapping[str, PickerOption] = MappingProxyType({o.label: o for o in OPTIONS})


def picker_option(label: str) -> PickerOption:
    """The option shown as *label*; ``UnknownOption`` for any other."""
    try:
        return OPTIONS_BY_LABEL[label]
    except KeyError:
        raise UnknownOption(f"unknown picker option {label!r}; choose from "
                            f"{', '.join(OPTIONS_BY_LABEL)}") from None


def option_spec(option: PickerOption, arms: Mapping[str, ArmSpec]) -> ArmSpec:
    """The spec that opens *option*: the arm's own, or the router's for its map."""
    spec = arms[option.arm]
    if option.map_name is None:
        return spec
    if spec.with_map is None:
        raise UnknownOption(f"{option.arm} takes no routing map")
    return spec.with_map(option.map_name)
