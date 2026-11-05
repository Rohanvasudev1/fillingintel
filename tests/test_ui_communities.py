"""graph_global's communities for the answer trace, read by community_id (Step 14 ticket 01).

On ``neo4j-test`` with the graph_global test graph.  Needs ``NEO4J_TEST_URI``.
"""
import pytest

from graph.constraints import apply_constraints
from graph.write import write_batch
from ingest.chunker import chunk_filing
from tests.global_data import SUMMARIES, TITLES, community_batch
from tests.graph_local_data import graph_local_batch
from ui.communities import CommunityInfo, read_communities, summary_of


@pytest.fixture
def graph(neo4j_driver, nvda_10k_filing, nvda_10q_filing, amd_10k_filing):
    texts, sections = {}, {}
    for filing in (nvda_10k_filing, nvda_10q_filing, amd_10k_filing):
        for c in chunk_filing(filing):
            texts[c.chunk_id] = filing.text[c.char_start:c.char_end]
            sections[c.chunk_id] = c.section
    apply_constraints(neo4j_driver)
    write_batch(neo4j_driver, community_batch(graph_local_batch(texts, sections)))
    return neo4j_driver


def test_each_selected_community_has_its_title_and_summary(graph):
    found = read_communities(graph, ("lower-export", "top-a"))
    assert found == {
        "lower-export": CommunityInfo("lower-export", TITLES["lower-export"],
                                      SUMMARIES["lower-export"]),
        "top-a": CommunityInfo("top-a", TITLES["top-a"], SUMMARIES["top-a"]),
    }


def test_a_community_with_no_report_or_not_in_the_graph_has_no_title(graph):
    found = read_communities(graph, ("lower-lone", "lower-gone"))
    assert found["lower-lone"] == CommunityInfo("lower-lone", None, None)
    assert "lower-gone" not in found


def test_no_ids_reads_nothing(graph):
    assert read_communities(graph, ()) == {}


def test_the_summary_is_the_reports_first_line_unless_it_is_a_finding():
    assert summary_of("TSMC makes the wafers.\n- A finding.") == "TSMC makes the wafers."
    assert summary_of("- Only a finding.") is None
    assert summary_of(None) is None
