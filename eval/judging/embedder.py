"""The Voyage query embedder as a Ragas embedding, for answer relevancy (Step 5, ticket 07).

Ragas 0.4.3 has no Voyage provider and its collections metrics accept only a
``BaseRagasEmbedding``, so this wrapper adapts the project's query embedder.
Texts are embedded with input type ``query``: answer relevancy compares the
question with questions generated from the answer.  The caller passes the cached
embedder, so a rerun makes no Voyage calls.
"""
from __future__ import annotations

from typing import Any

from ragas.embeddings.base import BaseRagasEmbedding

from ingest.voyage import DEFAULT_MODEL, QueryEmbedder


class VoyageRagasEmbedding(BaseRagasEmbedding):
    """Create one per question and run; it counts the tokens Voyage reported.

    Ragas calls ``embed_text`` on the object it was given, so the token count is a
    running total on this short-lived object rather than a returned value.
    """

    def __init__(self, embedder: QueryEmbedder, model: str = DEFAULT_MODEL):
        super().__init__(cache=None)
        self._embedder = embedder
        self.model = model
        self.tokens = 0

    def embed_text(self, text: str, **kwargs: Any) -> list[float]:
        embedding = self._embedder.embed_query(text, self.model)
        self.tokens = self.tokens + embedding.api_token_count
        return list(embedding.vector)

    async def aembed_text(self, text: str, **kwargs: Any) -> list[float]:
        return self.embed_text(text)
