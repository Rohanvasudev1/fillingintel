"""The text each named node is embedded as, and the embedder seam (Step 9).

Two forms, chosen in the config on the tuning set: "Label: name", or
"name: first evidence span". Markdown escapes are dropped from both, as the
span check drops them. Both sides of a pair use the same Voyage input type.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from extract.spans import unescape
from ingest.voyage import InputType
from resolve.config import EmbedText
from resolve.nodes import ResolveNode
from retrieve.query_cache import CachedBatchEmbedder


class NodeEmbedder(Protocol):
    """One vector per text, in order."""

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


def embed_text(node: ResolveNode, form: EmbedText) -> str:
    if form == "label_name":
        return f"{node.label}: {unescape(node.name)}"
    return f"{unescape(node.name)}: {unescape(node.evidence[0].span)}"


class VoyageNodeEmbedder:
    """Embeds through the batched Voyage path and its disk cache."""

    def __init__(self, cache: CachedBatchEmbedder, model: str, input_type: InputType):
        self._cache = cache
        self._model = model
        self._input_type = input_type

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return self._cache.embed(texts, self._model, self._input_type).vectors
