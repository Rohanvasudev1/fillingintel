"""The structured-output JSON schema of the adjudicator's reply (Step 9).

Every field is required, with null standing for "not used", as the API's
structured outputs ask (docs/research/claude-extraction-structured-output.md).
The answer and the related kind are enums; every other rule (a kind only on
`related`, quotes in the right node's chunks) is checked in code.
"""
from __future__ import annotations

import copy

from resolve.decisions import Answer, RelatedKind

_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "related_kind", "quote_a", "chunk_id_a", "quote_b", "chunk_id_b",
                 "reason"],
    "properties": {
        "answer": {"type": "string", "enum": [a.value for a in Answer]},
        "related_kind": {"anyOf": [{"type": "string", "enum": [k.value for k in RelatedKind]},
                                   {"type": "null"}],
                         "description": "Only when the answer is related; otherwise null."},
        "quote_a": {"type": "string", "description": "Words copied exactly from a passage "
                                                     "listed under node A."},
        "chunk_id_a": {"type": "string", "description": "The ID of that passage."},
        "quote_b": {"type": "string", "description": "Words copied exactly from a passage "
                                                     "listed under node B."},
        "chunk_id_b": {"type": "string", "description": "The ID of that passage."},
        "reason": {"type": "string", "description": "One or two sentences on why."},
    },
}


def output_schema() -> dict[str, object]:
    """The reply schema; a new dict on every call."""
    return copy.deepcopy(_SCHEMA)
