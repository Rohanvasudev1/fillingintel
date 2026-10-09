"""Reading one field of a candidate, with the rejection reason when it is unusable (Step 8)."""
from __future__ import annotations

import math
from collections.abc import Mapping

from extract.outcomes import Reason
from extract.spans import unescape
from graph.ontology import CONFIDENCE_LEVELS


class Rejected(Exception):  # noqa: N818 (a control-flow signal, caught within this package)
    """A candidate breaks a rule; carries the reason and a readable message."""

    def __init__(self, reason: Reason, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def text(raw: Mapping[str, object], field: str) -> str:
    """The non-blank string in *field*, trimmed, with markdown escapes dropped.

    The model sees the parsed filing's markdown and sometimes copies its escapes
    into names; they are formatting, not part of the name or key.
    """
    value = raw.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise Rejected(Reason.MISSING_PROPERTY, f"{field} is required")
    if not isinstance(value, str):
        raise Rejected(Reason.WRONG_PROPERTY_TYPE,
                       f"{field} must be text, got {type(value).__name__}")
    cleaned = unescape(value).strip()
    if not cleaned:
        raise Rejected(Reason.MISSING_PROPERTY, f"{field} is required")
    return cleaned


def optional_text(raw: object, field: str) -> str | None:
    """What text() reads from *field* of *raw*, or None where text() would reject it."""
    if not isinstance(raw, Mapping):
        return None
    try:
        return text(raw, field)
    except Rejected:
        return None


def number(raw: Mapping[str, object], field: str, required: bool) -> float | None:
    """The finite number in *field*, as a float; None when absent and not required."""
    value = raw.get(field)
    if value is None:
        if required:
            raise Rejected(Reason.MISSING_PROPERTY, f"{field} is required")
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise Rejected(Reason.WRONG_PROPERTY_TYPE, f"{field} must be a number, got {value!r}")
    return float(value)


def choice(raw: Mapping[str, object], field: str, options: tuple[str, ...]) -> str:
    """The option *field* names, compared without regard to case."""
    value = text(raw, field)
    match = {o.casefold(): o for o in options}.get(value.casefold())
    if match is None:
        raise Rejected(Reason.WRONG_PROPERTY_TYPE,
                       f"{field} {value!r} is not one of {', '.join(options)}")
    return match


def confidence(raw: Mapping[str, object]) -> str:
    return choice(raw, "confidence", CONFIDENCE_LEVELS)


def lookup(name: str, options: Mapping[str, object]) -> str | None:
    """The key of *options* equal to *name* without regard to case, or None."""
    return {o.casefold(): o for o in options}.get(name.casefold())
