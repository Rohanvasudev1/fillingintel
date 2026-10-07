"""The shared valid test graph (Step 7).

Structural nodes for NVIDIA's FY2026 10-K fixture, Chunk nodes with the real
chunk IDs the chunker gives that filing, and hand-written extracted nodes and
edges that quote it: TSMC as NVIDIA's foundry, the Compute & Networking
segment, Jen-Hsun Huang's two roles, export controls, and the share of revenue
from the largest direct customer. Every quote is checked to be a substring of
its chunk's text, so a bad quote or a chunker change fails the fixture.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pytest

from graph.batch import Batch, EdgeRecord, Evidence, NodeRecord, NodeRef

NVDA_CIK = "1045810"
NVDA_10K = "0001045810-26-000021"


def _chunk(ordinal: int) -> str:
    return f"{NVDA_10K}:{ordinal:04d}"


SEGMENTS_CHUNK = _chunk(6)
SUPPLY_CHUNK = _chunk(12)
EXPORT_CHUNK = _chunk(13)
OFFICERS_CHUNK = _chunk(16)
CUSTOMERS_CHUNK = _chunk(36)
MDA_SEGMENTS_CHUNK = _chunk(61)
MDA_CUSTOMERS_CHUNK = _chunk(67)

QUOTES = {
    SEGMENTS_CHUNK: "The Compute & Networking segment includes our Data Center accelerated "
                    "computing and networking platforms",
    SUPPLY_CHUNK: "We utilize foundries, such as Taiwan Semiconductor Manufacturing Company "
                  "Limited, or TSMC",
    EXPORT_CHUNK: "we have been subject to a series of shifting and expanding export control "
                  "restrictions",
    OFFICERS_CHUNK: "| Jen-Hsun Huang | 63 | President and Chief Executive Officer |",
    CUSTOMERS_CHUNK: "sales to one direct customer represented 22% of total revenue",
    MDA_SEGMENTS_CHUNK: 'Our two operating segments are "Compute & Networking" and "Graphics',
    MDA_CUSTOMERS_CHUNK: "sales to one direct customer represented 22% of total revenue",
}

NVIDIA = NodeRef.of("Company", cik=NVDA_CIK)
FILING = NodeRef.of("Filing", accession_no=NVDA_10K)
PERIOD = NodeRef.of("Period", cik=NVDA_CIK, fiscal_period="FY2026")
TSMC = NodeRef.of("Organization", key="org:tsmc")
COMPUTE = NodeRef.of("Segment", key="segment:nvda:compute-networking")
HUANG = NodeRef.of("Person", key="person:jen-hsun-huang")
EXPORT_CONTROLS = NodeRef.of("Regulation", key="regulation:us-export-controls")
TOP_CUSTOMER_SHARE = NodeRef.of(
    "MetricValue", key=f"metric:{NVDA_10K}:nvda:top-direct-customer-share:FY2026",
)


def evidence(chunk_id: str) -> Evidence:
    """The node evidence for *chunk_id*, quoting QUOTES."""
    return Evidence(chunk_id, QUOTES[chunk_id])


def edge_evidence(*chunk_ids: str) -> dict[str, list[str]]:
    """chunk_ids and evidence_spans properties for an extracted edge, quoting QUOTES."""
    return {"chunk_ids": list(chunk_ids), "evidence_spans": [QUOTES[c] for c in chunk_ids]}


def _structural_nodes(sections: Mapping[str, str]) -> tuple[NodeRecord, ...]:
    return (
        NodeRecord("Company", {"cik": NVDA_CIK, "name": "NVIDIA CORP", "ticker": "NVDA"}),
        NodeRecord("Filing", {
            "accession_no": NVDA_10K, "form_type": "10-K",
            "filing_date": date(2026, 2, 26), "fiscal_period": "FY2026",
        }),
        NodeRecord("Period", {"cik": NVDA_CIK, "fiscal_period": "FY2026"}),
        *(
            NodeRecord("Chunk", {
                "chunk_id": chunk_id, "accession_no": NVDA_10K, "section": sections[chunk_id],
            })
            for chunk_id in QUOTES
        ),
    )


def _extracted_nodes() -> tuple[NodeRecord, ...]:
    return (
        NodeRecord("Organization", {"key": TSMC.key_dict()["key"],
                                    "name": "Taiwan Semiconductor Manufacturing Company Limited"},
                   (evidence(SUPPLY_CHUNK),)),
        NodeRecord("Segment", {"key": COMPUTE.key_dict()["key"], "name": "Compute & Networking"},
                   (evidence(SEGMENTS_CHUNK),)),
        NodeRecord("Person", {"key": HUANG.key_dict()["key"], "name": "Jen-Hsun Huang"},
                   (evidence(OFFICERS_CHUNK),)),
        NodeRecord("Regulation", {"key": EXPORT_CONTROLS.key_dict()["key"],
                                  "name": "U.S. export controls"},
                   (evidence(EXPORT_CHUNK),)),
        NodeRecord("MetricValue", {
            "key": TOP_CUSTOMER_SHARE.key_dict()["key"],
            "concept": "share of total revenue from the largest direct customer",
            "value": 22,  # an int: the write path stores it as a float
            "unit": "percent", "period": "FY2026",
        }, (evidence(CUSTOMERS_CHUNK),)),
    )


def _edges() -> tuple[EdgeRecord, ...]:
    return (
        EdgeRecord("FILED", NVIDIA, FILING),
        EdgeRecord("COVERS_PERIOD", FILING, PERIOD),
        *(
            EdgeRecord("PART_OF", NodeRef.of("Chunk", chunk_id=chunk_id), FILING)
            for chunk_id in QUOTES
        ),
        EdgeRecord("SUPPLIES", TSMC, NVIDIA, edge_evidence(SUPPLY_CHUNK)),
        EdgeRecord("HAS_SEGMENT", NVIDIA, COMPUTE, edge_evidence(SEGMENTS_CHUNK)),
        EdgeRecord("SUBJECT_TO", NVIDIA, EXPORT_CONTROLS, edge_evidence(EXPORT_CHUNK)),
        EdgeRecord("HOLDS_ROLE_AT", HUANG, NVIDIA,
                   {"role": "President", **edge_evidence(OFFICERS_CHUNK)}),
        EdgeRecord("HOLDS_ROLE_AT", HUANG, NVIDIA,
                   {"role": "Chief Executive Officer", **edge_evidence(OFFICERS_CHUNK)}),
        EdgeRecord("REPORTS", FILING, TOP_CUSTOMER_SHARE, edge_evidence(CUSTOMERS_CHUNK)),
        EdgeRecord("MEASURES", TOP_CUSTOMER_SHARE, NVIDIA, edge_evidence(CUSTOMERS_CHUNK)),
    )


def check_spans(batch: Batch, chunk_texts: Mapping[str, str]) -> None:
    """Fail unless every evidence span in *batch* is a substring of its chunk's text."""
    pairs = [(e.chunk_id, e.span) for n in batch.nodes for e in n.evidence]
    for edge in batch.edges:
        pairs += zip(edge.properties.get("chunk_ids", ()),
                     edge.properties.get("evidence_spans", ()), strict=True)
    bad = [(c, s) for c, s in pairs if s not in chunk_texts.get(c, "")]
    assert not bad, f"spans not found in their chunks: {bad}"


def valid_test_batch(chunk_texts: Mapping[str, str], sections: Mapping[str, str]) -> Batch:
    """The valid test graph as one batch; its spans are checked against *chunk_texts*."""
    batch = Batch(nodes=(*_structural_nodes(sections), *_extracted_nodes()), edges=_edges())
    check_spans(batch, chunk_texts)
    return batch


@pytest.fixture(scope="session")
def nvda_10k_chunks(nvda_10k_filing):
    """chunk_id -> (section, text) for every chunk of the NVIDIA 10-K fixture."""
    from ingest.chunker import chunk_filing

    text = nvda_10k_filing.text
    return {
        c.chunk_id: (c.section, text[c.char_start:c.char_end])
        for c in chunk_filing(nvda_10k_filing)
    }


@pytest.fixture(scope="session")
def chunk_texts(nvda_10k_chunks):
    return {chunk_id: text for chunk_id, (_, text) in nvda_10k_chunks.items()}


@pytest.fixture(scope="session")
def graph_test_batch(nvda_10k_chunks, chunk_texts):
    """The valid test graph, built once per session."""
    sections = {chunk_id: section for chunk_id, (section, _) in nvda_10k_chunks.items()}
    return valid_test_batch(chunk_texts, sections)
