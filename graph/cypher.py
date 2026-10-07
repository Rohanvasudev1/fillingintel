"""Names placed in Cypher text (Step 7).

Labels, edge types, property keys and constraint names cannot be query
parameters, so they reach Cypher only from the ontology, checked against a
plain-identifier pattern and backtick-quoted. Every value is a parameter.
"""
from __future__ import annotations

import re

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def quoted(name: str) -> str:
    """*name* in backticks; raises ValueError unless it is a plain identifier."""
    if not _IDENTIFIER.match(name):
        raise ValueError(f"not a plain identifier: {name!r}")
    return f"`{name}`"
