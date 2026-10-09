"""What extraction reports besides records: rejections, flags, conflicts, failures (Step 8).

Rejection and flag reasons are closed enums, so every run counts them the
same way.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class Reason(StrEnum):
    """Why a candidate node or triple was rejected."""

    UNKNOWN_LABEL = "unknown_label"
    UNKNOWN_EDGE_TYPE = "unknown_edge_type"
    DISALLOWED_ENDPOINT = "disallowed_endpoint"  # endpoint pair not allowed
    UNKNOWN_FILER_REFERENCE = "unknown_filer_reference"
    DANGLING_LOCAL_ID = "dangling_local_id"
    SPAN_NOT_FOUND = "span_not_found"
    ENDPOINT_NODE_REJECTED = "endpoint_node_rejected"
    MISSING_PROPERTY = "missing_property"
    WRONG_PROPERTY_TYPE = "wrong_property_type"
    STRUCTURAL_LABEL = "structural_label"  # a structural label or edge type emitted


class CandidateKind(StrEnum):
    NODE = "node"
    TRIPLE = "triple"


class FlagReason(StrEnum):
    """Why a kept candidate is marked for review."""

    NAME_NOT_IN_SPAN = "name_not_in_span"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class Rejection:
    """One candidate thrown away: from which chunk, why, and the candidate as returned."""

    chunk_id: str
    kind: CandidateKind
    reason: Reason
    message: str
    candidate: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate", MappingProxyType(dict(self.candidate)))


@dataclass(frozen=True, slots=True)
class Flag:
    """One kept node or edge marked for review; `item` names it."""

    chunk_id: str
    reason: FlagReason
    item: str


@dataclass(frozen=True, slots=True)
class Conflict:
    """Two chunks gave one node or edge different values of a single-valued property."""

    item: str
    property: str
    kept: object
    other: object
    kept_chunk: str
    other_chunk: str


@dataclass(frozen=True, slots=True)
class ChunkFailure:
    """A chunk with no usable reply: an API error, a cut-off or refused reply, or bad JSON."""

    chunk_id: str
    reason: str
