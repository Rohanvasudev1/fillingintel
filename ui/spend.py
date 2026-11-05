"""The UI's per-session spend cap (Step 14).

A live question is refused before any call when its estimate plus what the
session has spent would pass ``FILINGINTEL_UI_MAX_SPEND`` (default $1.00).
After an answer, the session adds only what the calls made now cost: anything
replayed from a cache adds nothing.  UI spend is never benchmark spend; nothing
here reads or writes a step's spend files.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

from retrieve.arm import ArmResult
from ui.errors import SpendCapReached, SpendConfigError

SPEND_ENV = "FILINGINTEL_UI_MAX_SPEND"
DEFAULT_MAX_SPEND = 1.00
_TOLERANCE_USD = 1e-9  # so 0.3 + 0.7 does not pass a $1.00 cap through float rounding


def max_spend(environ: Mapping[str, str]) -> float:
    """The session cap in USD from ``FILINGINTEL_UI_MAX_SPEND``, or the default."""
    raw = environ.get(SPEND_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_SPEND
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0:
        raise SpendConfigError(f"{SPEND_ENV} must be a dollar amount such as 1.00, not {raw!r}")
    return value


def check_spend(*, estimate: float, spent: float, cap: float) -> None:
    """Raise ``SpendCapReached`` when *estimate* plus *spent* passes *cap*."""
    if estimate + spent > cap + _TOLERANCE_USD:
        raise SpendCapReached(
            f"This question is estimated at ${estimate:.2f} and this session has spent "
            f"${spent:.2f}; that would pass the ${cap:.2f} cap ({SPEND_ENV}). "
            "Sample questions still replay for free.")


def fresh_cost(result: ArmResult) -> float:
    """USD of the calls *result* made now: each one replayed from a cache costs nothing.

    graph_global's map calls are counted one by one, since only some may replay;
    its ``global_map`` stage, their sum, is left out so nothing counts twice.
    """
    total = 0.0 if result.query_cached else result.embed_cost_usd
    graph = result.graph
    if graph is not None:
        total += sum(stage.cost_usd for name, stage in graph.stages.items()
                     if not stage.from_cache and name != "global_map")
        if graph.global_search is not None:
            total += sum(c.cost.cost_usd for c in graph.global_search.calls
                         if not c.cost.from_cache)
    if result.routing is not None and not result.routing.cost.from_cache:
        total += result.routing.cost.cost_usd
    if not result.answer.from_cache:
        total += result.answer.cost_usd
    return total
