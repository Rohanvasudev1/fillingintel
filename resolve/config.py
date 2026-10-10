"""The frozen settings of one resolution run, and their hash (Step 9).

Every cutoff a candidate rule uses and every embedding choice lives here, so a
run report, a merge-log event and (later) a labelled pair can name the exact
settings by one hash. The values are starting points; ticket 06 tunes them on
the tuning set and then freezes them.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

from ingest.voyage import DEFAULT_MODEL, MODELS, InputType

EmbedText = Literal["label_name", "name_span"]
_EMBED_TEXTS = ("label_name", "name_span")
_INPUT_TYPES = ("query", "document")
FUZZY_OFF = 101.0  # a cutoff above any token_set_ratio turns the rule off


@dataclass(frozen=True)
class ResolveConfig:
    """Candidate-rule cutoffs and embedding choices."""

    fuzzy_cutoff: float = 90.0  # RapidFuzz token_set_ratio, 0-100; FUZZY_OFF turns it off
    top_k: int = 3  # embedding neighbours per node; 0 turns the rule off
    rare_word_max_keys: int = 3  # a word in at most this many match keys of a label; 0 off
    rare_word_min_length: int = 3
    embedding_model: str = DEFAULT_MODEL
    embed_text: EmbedText = "label_name"  # "Label: name", or "name: first evidence span"
    embed_input_type: InputType = "document"  # the same on both sides of a pair

    def __post_init__(self) -> None:
        if not 0 <= self.fuzzy_cutoff <= FUZZY_OFF:
            raise ValueError(f"fuzzy_cutoff must be 0 to {FUZZY_OFF}")
        for name in ("top_k", "rare_word_max_keys", "rare_word_min_length"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a whole number of at least 0")
        if self.embedding_model not in MODELS:
            raise ValueError(f"unknown embedding model {self.embedding_model!r}")
        if self.embed_text not in _EMBED_TEXTS:
            raise ValueError(f"embed_text must be one of {_EMBED_TEXTS}")
        if self.embed_input_type not in _INPUT_TYPES:
            raise ValueError(f"embed_input_type must be one of {_INPUT_TYPES}")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def config_hash(self) -> str:
        """SHA-256 of the settings as sorted JSON; any change gives a new hash."""
        text = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
