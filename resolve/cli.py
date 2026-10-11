"""Argument parsing shared by the resolve commands (Step 9)."""
from __future__ import annotations

import argparse


def cost_cap(value: str) -> float:
    """An argparse type for --max-cost: a number of USD, 0 or more."""
    try:
        cap = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from None
    if not cap >= 0:  # also refuses nan
        raise argparse.ArgumentTypeError("--max-cost must be 0 or more")
    return cap
