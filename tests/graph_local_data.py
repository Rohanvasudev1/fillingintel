"""The graph_local test graph and fakes (Step 11 ticket 05).

The shared valid test graph (``tests/graph_test_data.py``) plus NVIDIA's FY2027-Q2
10-Q fixture filing, the Blackwell product with evidence in both NVIDIA filings,
the Data Center market platform offering it, and one export-control risk factor
in each filing joined by a persistence link.  Every quote is checked against its
real chunk's text.

Node embeddings are fakes: each node text gets a unit vector whose cosine with
the fake query vector (the first axis) is the similarity a test chooses, so a
test states its similarities and its expected ranking directly.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

from graph.batch import Batch, EdgeRecord, Evidence, NodeRecord, NodeRef
from ingest.voyage import BatchEmbedding
from tests.graph_test_data import (
    COMPUTE,
    DATA_CENTER,
    EXPORT_CONTROLS,
    FILING,
    HUANG,
    NVDA_10K,
    NVDA_CIK,
    NVIDIA,
    TEST_PROMPT,
    TOP_CUSTOMER_SHARE,
    TSMC,
    check_spans,
    valid_test_batch,
)

DIMS = 1024
NVDA_10Q = "0001045810-26-000075"  # FY2027-Q2
K_BLACKWELL_CHUNK = f"{NVDA_10K}:0061"
Q_BLACKWELL_CHUNK = f"{NVDA_10Q}:0035"
K_RISK_CHUNK = f"{NVDA_10K}:0041"
Q_RISK_CHUNK = f"{NVDA_10Q}:0057"
PERSIST_PROMPT = "persist/exact_title"

QUOTES = {
    K_BLACKWELL_CHUNK: "Our Blackwell architectures represented the majority of our Data "
                       "Center revenue",
    Q_BLACKWELL_CHUNK: "Blackwell continued to account for the majority of our system shipments",
    K_RISK_CHUNK: "export controls has negatively impacted and may in the future negatively "
                  "impact demand for our products",
    Q_RISK_CHUNK: "export controls has negatively impacted and may in the future negatively "
                  "impact demand for our products",
}

Q_FILING = NodeRef.of("Filing", accession_no=NVDA_10Q)
Q_PERIOD = NodeRef.of("Period", cik=NVDA_CIK, fiscal_period="FY2027-Q2")
BLACKWELL = NodeRef.of("Product", key="Product:blackwell")
K_RISK_TITLE = "Export controls may reduce demand for our products"
Q_RISK_TITLE = "Export controls have reduced and may reduce demand for our products"
K_RISK = NodeRef.of("RiskFactor", key=f"{NVDA_10K}:export controls may reduce demand")
Q_RISK = NodeRef.of("RiskFactor", key=f"{NVDA_10Q}:export controls have reduced demand")

# Each node's embedded text, as graph_local builds it.
TEXTS = {
    TSMC: "Organization: Taiwan Semiconductor Manufacturing Company Limited",
    COMPUTE: "Segment: Compute & Networking",
    DATA_CENTER: "MarketPlatform: Data Center",
    HUANG: "Person: Jen-Hsun Huang",
    EXPORT_CONTROLS: "Regulation: U.S. export controls",
    TOP_CUSTOMER_SHARE: "MetricValue: share of total revenue from the largest direct "
                        "customer, FY2026",
    BLACKWELL: "Product: Blackwell",
    K_RISK: f"RiskFactor: {K_RISK_TITLE}",
    Q_RISK: f"RiskFactor: {Q_RISK_TITLE}",
}
EXTRACTED = tuple(TEXTS)


def key_of(ref: NodeRef) -> str:
    return ref.key_dict()["key"]


def _evidence(chunk_id: str) -> Evidence:
    return Evidence(chunk_id, QUOTES[chunk_id], "stated", TEST_PROMPT)


def _edge_evidence(*chunk_ids: str, prompt: str = TEST_PROMPT) -> dict[str, list[str]]:
    return {
        "chunk_ids": list(chunk_ids),
        "evidence_spans": [QUOTES[c] for c in chunk_ids],
        "confidences": ["stated"] * len(chunk_ids),
        "extract_prompts": [prompt] * len(chunk_ids),
    }


def _chunk_node(chunk_id: str, section: str) -> NodeRecord:
    return NodeRecord("Chunk", {"chunk_id": chunk_id, "accession_no": chunk_id.split(":")[0],
                                "section": section})


def graph_local_batch(chunk_texts: Mapping[str, str], sections: Mapping[str, str]) -> Batch:
    """The test graph as one batch; every quote is checked against *chunk_texts*."""
    base = valid_test_batch(chunk_texts, sections)
    new_chunks = (Q_BLACKWELL_CHUNK, K_RISK_CHUNK, Q_RISK_CHUNK)  # the 10-K's 0061 is in base
    nodes = (
        NodeRecord("Filing", {"accession_no": NVDA_10Q, "form_type": "10-Q",
                              "filing_date": date(2026, 8, 28), "fiscal_period": "FY2027-Q2"}),
        NodeRecord("Period", {"cik": NVDA_CIK, "fiscal_period": "FY2027-Q2"}),
        *(_chunk_node(c, sections[c]) for c in new_chunks),
        NodeRecord("Product", {"key": key_of(BLACKWELL), "name": "Blackwell"},
                   (_evidence(K_BLACKWELL_CHUNK), _evidence(Q_BLACKWELL_CHUNK))),
        NodeRecord("RiskFactor", {"key": key_of(K_RISK), "title": K_RISK_TITLE},
                   (_evidence(K_RISK_CHUNK),)),
        NodeRecord("RiskFactor", {"key": key_of(Q_RISK), "title": Q_RISK_TITLE},
                   (_evidence(Q_RISK_CHUNK),)),
    )
    edges = (
        EdgeRecord("FILED", NVIDIA, Q_FILING),
        EdgeRecord("COVERS_PERIOD", Q_FILING, Q_PERIOD),
        *(EdgeRecord("PART_OF", NodeRef.of("Chunk", chunk_id=c),
                     NodeRef.of("Filing", accession_no=c.split(":")[0])) for c in new_chunks),
        EdgeRecord("OFFERS", DATA_CENTER, BLACKWELL, _edge_evidence(K_BLACKWELL_CHUNK)),
        EdgeRecord("DISCLOSES", FILING, K_RISK, _edge_evidence(K_RISK_CHUNK)),
        EdgeRecord("DISCLOSES", Q_FILING, Q_RISK, _edge_evidence(Q_RISK_CHUNK)),
        EdgeRecord("PERSISTS_AS", K_RISK, Q_RISK,
                   _edge_evidence(K_RISK_CHUNK, Q_RISK_CHUNK, prompt=PERSIST_PROMPT)),
    )
    batch = Batch(nodes=(*base.nodes, *nodes), edges=(*base.edges, *edges))
    check_spans(batch, chunk_texts)
    return batch


def at_similarity(similarity: float) -> tuple[float, ...]:
    """A unit vector whose cosine with ``QUERY_VECTOR`` is *similarity*."""
    return (similarity, math.sqrt(1.0 - similarity ** 2)) + (0.0,) * (DIMS - 2)


QUERY_VECTOR = at_similarity(1.0)


class FakeNodeEmbedder:
    """A batch embedder giving each node text the similarity a test chose (default 0.01)."""

    def __init__(self, similarities: Mapping[NodeRef, float]):
        self._by_text = {TEXTS[ref]: s for ref, s in similarities.items()}
        self.texts: list[str] = []

    def embed_batch(self, texts, model, input_type) -> BatchEmbedding:
        self.texts += texts
        vectors = tuple(at_similarity(self._by_text.get(t, 0.01)) for t in texts)
        return BatchEmbedding(vectors=vectors, total_tokens=5 * len(texts))

