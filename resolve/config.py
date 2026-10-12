"""The frozen settings of one resolution run, and their hash (Steps 9, 9b).

Every cutoff a candidate rule uses, every embedding choice, and (Step 9b) the
group rule and name limit live here, so a
run report, a merge-log event and (later) a labelled pair can name the exact
settings by one hash. The defaults are the values chosen on the tuning set in
ticket 06 (resolve.tuning.CONFIGS holds the starting values and the others
compared) and frozen in benchmarks/resolution/frozen.json.
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
# How held-back groups are handled. "group_cut/v1" (Step 9b) cuts a group holding a
# `related` or `distinct` pair into pieces (resolve.cut); Step 9 held it back whole
# and had no such field, so its hashes come from configs without it.
GROUP_RULES = ("group_cut/v1",)
GROUP_FIELDS = ("max_group_names", "group_rule")  # settings the tuning report did not compare


@dataclass(frozen=True)
class ResolveConfig:
    """Candidate-rule cutoffs and embedding choices."""

    fuzzy_cutoff: float = 85.0  # RapidFuzz token_set_ratio, 0-100; FUZZY_OFF turns it off
    top_k: int = 3  # embedding neighbours per node; 0 turns the rule off
    rare_word_max_keys: int = 0  # a word in at most this many match keys of a label; 0 off
    rare_word_min_length: int = 3
    embedding_model: str = DEFAULT_MODEL
    embed_text: EmbedText = "label_name"  # "Label: name", or "name: first evidence span"
    embed_input_type: InputType = "document"  # the same on both sides of a pair
    max_group_names: int = 12  # names in one merged group or piece; also bounds the cut search
    group_rule: str = "group_cut/v1"

    def __post_init__(self) -> None:
        if not 0 <= self.fuzzy_cutoff <= FUZZY_OFF:
            raise ValueError(f"fuzzy_cutoff must be 0 to {FUZZY_OFF}")
        if isinstance(self.max_group_names, bool) or not isinstance(self.max_group_names, int) \
                or self.max_group_names < 2:
            raise ValueError("max_group_names must be a whole number of at least 2")
        if self.group_rule not in GROUP_RULES:
            raise ValueError(f"group_rule must be one of {GROUP_RULES}")
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

    def candidate_rules(self) -> dict[str, object]:
        """The settings without the group fields: what Step 9's tuning report compared."""
        return {k: v for k, v in self.to_dict().items() if k not in GROUP_FIELDS}

    @property
    def config_hash(self) -> str:
        """SHA-256 of the settings as sorted JSON; any change gives a new hash."""
        text = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
