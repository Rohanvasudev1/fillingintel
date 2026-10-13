"""The structured-output JSON schema of the persistence adjudicator's reply (Step 10).

Every field is required, as the API's structured outputs ask. The answer is an
enum; that each quote is in its own risk factor's passages is checked in code.
"""
from __future__ import annotations

import copy

from persist.decisions import Answer

FIELDS = ("answer", "quote_earlier", "chunk_id_earlier", "quote_later", "chunk_id_later",
          "reason")
_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(FIELDS),
    "properties": {
        "answer": {"type": "string", "enum": [a.value for a in Answer]},
        "quote_earlier": {"type": "string", "description": "Words copied exactly from a "
                                                           "passage listed under the earlier "
                                                           "risk factor."},
        "chunk_id_earlier": {"type": "string", "description": "The ID of that passage."},
        "quote_later": {"type": "string", "description": "Words copied exactly from a passage "
                                                         "listed under the later risk factor."},
        "chunk_id_later": {"type": "string", "description": "The ID of that passage."},
        "reason": {"type": "string", "description": "One or two sentences on why."},
    },
}


def output_schema() -> dict[str, object]:
    """The reply schema; a new dict on every call."""
    return copy.deepcopy(_SCHEMA)
