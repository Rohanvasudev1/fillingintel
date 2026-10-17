"""The graph_global test setting, shared by its tests and the capture script (Step 12).

The graph is ``tests/graph_local_data.py``'s, around real fixture chunk IDs, with
four lower communities and two top ones written over it.  Report vectors and
chunk vectors are fakes whose cosine with the query (the first axis) is the
similarity stated here, so the expected order follows from the rule.

The recorded questions are two real dev global questions.  Their filter keeps
the two 10-Ks, so on this graph each selects the same three communities,
showing ``SHOWN_10K``'s chunks.  Recorded map and reduce replies live in
``tests/fixtures/anthropic/global_{name}.json``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from graph.batch import Batch, EdgeRecord, NodeRecord, NodeRef
from graph.community import LOWER, TOP, member_hash
from graph.report_file import ReportEntry, ReportsFile
from ingest.voyage import BatchEmbedding
from retrieve.query_cache import CachedBatchEmbedder
from tests.graph_local_data import (
    BLACKWELL,
    K_BLACKWELL_CHUNK,
    K_RISK,
    K_RISK_CHUNK,
    Q_BLACKWELL_CHUNK,
    Q_RISK,
    Q_RISK_CHUNK,
    at_similarity,
    key_of,
)
from tests.graph_test_data import (
    COMPUTE,
    DATA_CENTER,
    EXPORT_CHUNK,
    EXPORT_CONTROLS,
    HUANG,
    OFFICERS_CHUNK,
    SEGMENTS_CHUNK,
    SUPPLY_CHUNK,
    TEST_REPORT_PROMPT,
    TSMC,
)
from tests.vector_fakes import MODEL

FIXTURES = Path(__file__).parent / "fixtures" / "anthropic"
LOADED = ("nvda_10k", "nvda_10q", "amd_10k")

MEMBERS = {
    "lower-supply": (TSMC, COMPUTE, DATA_CENTER),  # 10-K chunks 0006, 0012
    "lower-blackwell": (BLACKWELL, Q_RISK),  # 10-K 0061; 10-Q 0035, 0057
    "lower-export": (EXPORT_CONTROLS, K_RISK),  # 10-K 0013, 0041
    "lower-lone": (HUANG,),  # no report
}
CHILDREN = {"top-a": ("lower-supply", "lower-lone"),
            "top-b": ("lower-blackwell", "lower-export")}
TITLES = {
    "lower-supply": "TSMC wafer supply for NVIDIA's compute products",
    "lower-blackwell": "Blackwell and the export-control demand risk",
    "lower-export": "U.S. export controls on NVIDIA's data center products",
    "top-a": "NVIDIA's supply chain and leadership",
    "top-b": "Blackwell demand under export controls",
}
SUMMARIES = {
    "lower-supply": "NVIDIA relies on TSMC to manufacture the wafers for its Compute & "
                    "Networking segment and its Data Center market platform.",
    "lower-blackwell": "Blackwell drives most of NVIDIA's Data Center revenue, while export "
                       "controls have reduced demand for its products.",
    "lower-export": "U.S. export controls restrict NVIDIA's sales of data center products to "
                    "China and other markets.",
    "top-a": "NVIDIA's foundry dependence on TSMC and its officers.",
    "top-b": "How export controls weigh on Blackwell's data center demand.",
}
REPORTED = tuple(TITLES)
CHUNK_SIMILARITIES = {
    SUPPLY_CHUNK: 0.7, SEGMENTS_CHUNK: 0.6, K_BLACKWELL_CHUNK: 0.5, Q_BLACKWELL_CHUNK: 0.8,
    Q_RISK_CHUNK: 0.4, EXPORT_CHUNK: 0.3, K_RISK_CHUNK: 0.9, OFFICERS_CHUNK: 0.2,
}
SIMILARITIES = {"lower-supply": 0.5, "lower-blackwell": 0.9, "lower-export": 0.7,
                "top-a": 0.8, "top-b": 0.6}

UNFILTERED = "What are the main supply chain risks?"
NVDA_FY2026 = "What did NVIDIA say about supply in fiscal 2026?"  # the 10-K only
AMD_FY2025 = "What did AMD's fiscal 2025 10-K say about competition?"  # no community
QUESTIONS = {  # dev global questions q0030 and q0023, verbatim
    "foundries": "How does dependence on external foundries, and on TSMC in particular, differ "
                 "across NVIDIA, AMD and Intel according to their latest 10-K risk factors?",
    "export": "Beyond lost direct sales, what knock-on competitive effects of U.S.-China export "
              "controls and trade tensions do NVIDIA, AMD and Intel describe in their latest "
              "10-Ks?",
}
SHOWN_10K = {  # what each recorded question's map calls are shown, by community rank
    "lower-blackwell": (K_BLACKWELL_CHUNK,),
    "lower-export": (K_RISK_CHUNK, EXPORT_CHUNK),
    "lower-supply": (SUPPLY_CHUNK, SEGMENTS_CHUNK),
}


def keys(community_id: str) -> list[str]:
    refs = MEMBERS.get(community_id) or [m for c in CHILDREN[community_id] for m in MEMBERS[c]]
    return [key_of(r) for r in refs]


def report_text(community_id: str) -> str:
    """What a community's report is embedded as."""
    return f"{TITLES[community_id]}: {SUMMARIES[community_id]}"


def _community(community_id: str) -> NodeRecord:
    level = TOP if community_id in CHILDREN else LOWER
    props = {"community_id": community_id, "level": level,
             "member_hash": member_hash(keys(community_id)), "report_chunk_ids": []}
    if community_id in REPORTED:
        props |= {"title": TITLES[community_id], "report": SUMMARIES[community_id],
                  "report_prompt": TEST_REPORT_PROMPT}
    return NodeRecord("Community", props)


def _ref(community_id: str) -> NodeRef:
    return NodeRef.of("Community", community_id=community_id)


def community_batch(base: Batch) -> Batch:
    """*base* with the communities written over it."""
    nodes = tuple(_community(c) for c in (*MEMBERS, *CHILDREN))
    edges = (
        *(EdgeRecord("IN_COMMUNITY", m, _ref(c)) for c, ms in MEMBERS.items() for m in ms),
        *(EdgeRecord("PARENT_OF", _ref(t), _ref(c)) for t, cs in CHILDREN.items() for c in cs),
    )
    return Batch(nodes=(*base.nodes, *nodes), edges=(*base.edges, *edges))


def reports_file(**changed: dict) -> ReportsFile:
    """The reports file the test graph was loaded from; *changed* overrides an entry's fields."""
    entries = {}
    for community_id in REPORTED:
        level = TOP if community_id in CHILDREN else LOWER
        fields = {"member_hash": member_hash(keys(community_id)),
                  "title": TITLES[community_id], "summary": SUMMARIES[community_id],
                  **changed.get(community_id, {})}
        entries[community_id] = ReportEntry(
            community_id, fields["member_hash"], level, fields["title"], fields["summary"], (),
            TEST_REPORT_PROMPT, fields["summary"])
    return ReportsFile("reports-run1.jsonl", "r" * 64, "communities-run1.json", "c" * 64,
                       "a" * 64, True, MappingProxyType(entries))


class FakeReportEmbedder:
    def __init__(self, similarities: Mapping[str, float]):
        self._by_text = {report_text(c): s for c, s in similarities.items()}

    def embed_batch(self, texts, model, input_type) -> BatchEmbedding:
        return BatchEmbedding(vectors=tuple(at_similarity(self._by_text[t]) for t in texts),
                              total_tokens=5 * len(texts))


def report_cache(directory: Path, similarities: Mapping[str, float] = SIMILARITIES
                 ) -> CachedBatchEmbedder:
    """A report embedding cache holding a vector for each of *similarities*' reports."""
    cache = CachedBatchEmbedder(FakeReportEmbedder(similarities), cache_dir=directory)
    cache.embed([report_text(c) for c in similarities], MODEL, "document")
    return cache



def recorded(name: str) -> dict:
    """One recorded question: its map calls and reduce call, each with its request hash."""
    return json.loads((FIXTURES / f"global_{name}.json").read_text(encoding="utf-8"))
