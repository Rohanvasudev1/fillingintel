"""What a community report is written from (Step 12).

Batches are hand-built around the real chunk IDs and quotes of
tests/graph_test_data.py; the chunk texts are the NVIDIA FY2026 10-K
fixture's own, through the real parser and chunker.
"""
import pytest

from graph.batch import Batch, EdgeRecord, Evidence, NodeRecord, NodeRef
from graph.community import LOWER, TOP, Community, member_hash
from graph.report_input import (
    ChildReport,
    CommunityIndex,
    InputBudget,
    lower_input,
    rendered_tokens,
    top_input,
)
from tests.graph_test_data import (
    CUSTOMERS_CHUNK,
    EXPORT_CHUNK,
    NVDA_CIK,
    QUOTES,
    SEGMENTS_CHUNK,
    SUPPLY_CHUNK,
    TEST_PROMPT,
    edge_evidence,
)

NVIDIA = NodeRef.of("Company", cik=NVDA_CIK)


@pytest.fixture(scope="module")
def texts(nvda_10k_filing):
    from ingest.chunker import chunk_filing

    return {c.chunk_id: nvda_10k_filing.text[c.char_start:c.char_end]
            for c in chunk_filing(nvda_10k_filing)}


def _node(label, key, *chunk_ids, prop="name"):
    evidence = tuple(Evidence(c, QUOTES[c], "stated", TEST_PROMPT) for c in chunk_ids)
    return NodeRecord(label, {"key": key, prop: key.split(":")[-1]}, evidence)


def _ref(node):
    return NodeRef.of(node.label, key=node.properties["key"])


def _edge(type_, start, end, *chunk_ids):
    start = _ref(start) if isinstance(start, NodeRecord) else start
    end = _ref(end) if isinstance(end, NodeRecord) else end
    return EdgeRecord(type_, start, end, edge_evidence(*chunk_ids))


def _community(level, nodes, parent=None):
    keys = tuple(sorted(n.properties["key"] for n in nodes))
    digest = member_hash(keys)
    return Community(f"{level}-{digest[:12]}", level, keys, digest, parent, len(keys) >= 3)


TSMC = _node("Organization", "Organization:tsmc", SUPPLY_CHUNK, EXPORT_CHUNK)
BLACKWELL = _node("Product", "Product:blackwell", SUPPLY_CHUNK)
EXPORT = _node("Regulation", "Regulation:export controls", EXPORT_CHUNK)
RISK = _node("RiskFactor", "RiskFactor:supply risk", SUPPLY_CHUNK, prop="title")
OUTSIDER = _node("Organization", "Organization:outsider", CUSTOMERS_CHUNK, SEGMENTS_CHUNK)
BATCH = Batch(
    nodes=(NodeRecord("Company", {"cik": NVDA_CIK, "name": "NVIDIA CORP"}),
           TSMC, BLACKWELL, EXPORT, RISK, OUTSIDER),
    edges=(_edge("SUPPLIES", TSMC, NVIDIA, SUPPLY_CHUNK),  # a filer end: not a member edge
           _edge("SUBJECT_TO", BLACKWELL, EXPORT, EXPORT_CHUNK),
           _edge("AFFECTS", RISK, BLACKWELL, SUPPLY_CHUNK, EXPORT_CHUNK),
           _edge("SUPPLIES", OUTSIDER, TSMC, CUSTOMERS_CHUNK)),  # one end outside
)
LOWER_COMMUNITY = _community(LOWER, (TSMC, BLACKWELL, EXPORT, RISK))


def test_members_are_listed_with_label_and_name_or_title(texts):
    built = lower_input(LOWER_COMMUNITY, CommunityIndex.from_batch(BATCH), texts)
    assert [(m.label, m.name) for m in built.members] == [
        ("Organization", "tsmc"), ("Product", "blackwell"), ("Regulation", "export controls"),
        ("RiskFactor", "supply risk")]


def test_only_edges_with_both_ends_members_are_given_most_evidenced_first(texts):
    built = lower_input(LOWER_COMMUNITY, CommunityIndex.from_batch(BATCH), texts)
    assert [(e.type, e.start, e.end) for e in built.edges] == [
        ("AFFECTS", "RiskFactor:supply risk", "Product:blackwell"),
        ("SUBJECT_TO", "Product:blackwell", "Regulation:export controls")]
    assert built.edges[0].spans == (QUOTES[SUPPLY_CHUNK], QUOTES[EXPORT_CHUNK])


def test_passages_are_member_chunks_ranked_by_how_many_members_and_edges_cite_them(texts):
    built = lower_input(LOWER_COMMUNITY, CommunityIndex.from_batch(BATCH), texts)
    # SUPPLY: TSMC, Blackwell, risk and AFFECTS (4); EXPORT: TSMC, regulation, SUBJECT_TO,
    # AFFECTS (4); ties go to the smaller chunk ID. CUSTOMERS is only the outsider's.
    assert [p.chunk_id for p in built.passages] == [SUPPLY_CHUNK, EXPORT_CHUNK]
    assert built.passages[0].text == texts[SUPPLY_CHUNK]


def test_the_evidence_is_every_members_node_evidence(texts):
    built = lower_input(LOWER_COMMUNITY, CommunityIndex.from_batch(BATCH), texts)
    assert built.evidence == frozenset({SUPPLY_CHUNK, EXPORT_CHUNK})


def test_passages_stop_at_the_token_budget_but_the_first_is_always_given(texts):
    index = CommunityIndex.from_batch(BATCH)
    full = lower_input(LOWER_COMMUNITY, index, texts)
    tight = lower_input(LOWER_COMMUNITY, index, texts,
                        InputBudget(total=rendered_tokens(full) - 1))
    assert [p.chunk_id for p in tight.passages] == [SUPPLY_CHUNK]
    assert rendered_tokens(full) <= InputBudget().total
    nothing = lower_input(LOWER_COMMUNITY, index, texts, InputBudget(total=1, edges=1))
    assert [p.chunk_id for p in nothing.passages] == [SUPPLY_CHUNK] and nothing.edges == ()


def test_edge_spans_are_capped_per_edge(texts):
    built = lower_input(LOWER_COMMUNITY, CommunityIndex.from_batch(BATCH), texts,
                        InputBudget(spans_per_edge=1))
    assert built.edges[0].spans == (QUOTES[SUPPLY_CHUNK],)


def test_a_missing_chunk_text_is_an_error(texts):
    partial = {k: v for k, v in texts.items() if k != SUPPLY_CHUNK}
    with pytest.raises(ValueError, match=SUPPLY_CHUNK):
        lower_input(LOWER_COMMUNITY, CommunityIndex.from_batch(BATCH), partial)


def test_a_top_report_reads_its_children_and_may_cite_only_what_they_kept():
    top = _community(TOP, (TSMC, BLACKWELL, EXPORT, RISK, OUTSIDER))
    children = (ChildReport("lower-a", "Foundry supply", "TSMC makes chips [x].",
                            (SUPPLY_CHUNK,)),
                ChildReport("lower-b", "Empty", "", ()),
                ChildReport("lower-c", "Customers", "One customer [y].", (CUSTOMERS_CHUNK,)))
    built = top_input(top, children)
    assert [c.community_id for c in built.children] == ["lower-a", "lower-c"]
    assert built.evidence == frozenset({SUPPLY_CHUNK, CUSTOMERS_CHUNK})
    assert top_input(top, children[1:2]).children == ()
