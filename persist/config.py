"""The frozen settings of one persistence run, and their hash (Step 10).

The run report, the link file's provenance and (ticket 06) the freeze file
name these settings by one hash, as resolve.config does for resolution.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from ingest.voyage import DEFAULT_MODEL, MODELS, InputType

_INPUT_TYPES = ("query", "document")


@dataclass(frozen=True)
class PersistConfig:
    top_k: int = 3  # earlier neighbours sent to the adjudicator per later risk factor
    embedding_model: str = DEFAULT_MODEL
    embed_input_type: InputType = "document"  # the same on both sides of a pair

    def __post_init__(self) -> None:
        if isinstance(self.top_k, bool) or not isinstance(self.top_k, int) or self.top_k < 1:
            raise ValueError("top_k must be a whole number of at least 1")
        if self.embedding_model not in MODELS:
            raise ValueError(f"unknown embedding model {self.embedding_model!r}")
        if self.embed_input_type not in _INPUT_TYPES:
            raise ValueError(f"embed_input_type must be one of {_INPUT_TYPES}")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def config_hash(self) -> str:
        """SHA-256 of the settings as sorted JSON; any change gives a new hash."""
        text = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
