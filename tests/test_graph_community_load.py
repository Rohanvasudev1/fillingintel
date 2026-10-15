"""Communities and reports as graph records for the rebuild (Step 12 ticket 04). No database.

The communities and the reports file are built by hand around the valid test
graph, so the records must equal the test communities of graph_test_data,
which the validator tests load. Each refusal breaks one thing.
"""
import json
from dataclasses import replace
from types import MappingProxyType

import pytest

from graph.batch import check_batch
from graph.community import Community, member_hash
from graph.community_file import CommunitiesFile
from graph.community_graph import community_graph
from graph.community_load import CommunityInputs, CommunityMismatch, community_records
from graph.report_file import (
    ReportEntry,
    ReportsFile,
    ReportsFileError,
    latest_reports_file,
    read_reports_file,
)
from ingest.provenance import REPO_ROOT
from tests.graph_test_data import (
    LONE_COMMUNITY,
    LONE_MEMBERS,
    SUPPLY_COMMUNITY,
    SUPPLY_MEMBERS,
    TEST_REPORT_PROMPT,
    TOP_COMMUNITY,
    member_keys,
    with_communities,
)
from tests.graph_test_data import (
    community_records as expected_records,
)


def _id(ref):
    return ref.key_dict()["community_id"]


def _community(ref, level, members, parent, reportable=True):
    keys = tuple(sorted(member_keys(members)))
    return Community(_id(ref), level, keys, member_hash(keys), parent, reportable)


COMMUNITIES = (
    _community(TOP_COMMUNITY, "top", (*SUPPLY_MEMBERS, *LONE_MEMBERS), None),
    _community(SUPPLY_COMMUNITY, "lower", SUPPLY_MEMBERS, _id(TOP_COMMUNITY)),
    _community(LONE_COMMUNITY, "lower", LONE_MEMBERS, _id(TOP_COMMUNITY), reportable=False),
)


def _entries():
    """The reports of the reportable communities, as the expected records hold them."""
    nodes, _ = expected_records()
    entries = {}
    for node in nodes:
        props = node.properties
        if "report" in props:
            entries[props["community_id"]] = ReportEntry(
                props["community_id"], props["member_hash"], props["level"], props["title"],
                props["report"], tuple(props["report_chunk_ids"]), TEST_REPORT_PROMPT)
    return entries


@pytest.fixture
def inputs(graph_test_batch):
    input_sha = community_graph(graph_test_batch).input_sha256
    return CommunityInputs(
        CommunitiesFile("communities-run1.json", "c" * 64, input_sha, COMMUNITIES),
        ReportsFile("reports-run1.jsonl", "r" * 64, "communities-run1.json", "c" * 64,
                    input_sha, True, MappingProxyType(_entries())),
    )


def _shape(nodes, edges):
    return (sorted((dict(n.properties) for n in nodes), key=lambda p: p["community_id"]),
            sorted((e.type, str(e.start), str(e.end)) for e in edges))


def test_the_records_are_the_test_communities(graph_test_batch, inputs):
    records = community_records(graph_test_batch, inputs)

    assert _shape(records.nodes, records.edges) == _shape(*expected_records())
    assert check_batch(with_communities(graph_test_batch)) == ()


def test_an_unreportable_community_loads_with_no_report(graph_test_batch, inputs):
    records = community_records(graph_test_batch, inputs)

    lone = next(n for n in records.nodes if n.properties["community_id"] == _id(LONE_COMMUNITY))
    assert dict(lone.properties) == {
        "community_id": _id(LONE_COMMUNITY), "level": "lower",
        "member_hash": member_hash(member_keys(LONE_MEMBERS)), "report_chunk_ids": []}


def test_the_records_are_counted(graph_test_batch, inputs):
    counts = community_records(graph_test_batch, inputs).counts

    assert counts == {"top": 1, "lower": 2, "with_report": 2, "without_report": 1,
                      "members": 4}


def _without_report(inputs, community_id):
    entries = {k: v for k, v in inputs.reports.entries.items() if k != community_id}
    return replace(inputs, reports=replace(inputs.reports, entries=MappingProxyType(entries)))


def test_a_reportable_community_with_no_report_is_refused(graph_test_batch, inputs):
    with pytest.raises(CommunityMismatch, match=_id(SUPPLY_COMMUNITY)):
        community_records(graph_test_batch, _without_report(inputs, _id(SUPPLY_COMMUNITY)))


def test_a_report_for_other_members_is_refused(graph_test_batch, inputs):
    entries = dict(inputs.reports.entries)
    entries[_id(SUPPLY_COMMUNITY)] = replace(entries[_id(SUPPLY_COMMUNITY)],
                                             member_hash="0" * 64)
    stale = replace(inputs, reports=replace(inputs.reports, entries=MappingProxyType(entries)))

    with pytest.raises(CommunityMismatch, match="member hash"):
        community_records(graph_test_batch, stale)


def test_communities_from_another_community_graph_are_refused(graph_test_batch, inputs):
    stale = replace(inputs, communities=replace(inputs.communities, input_sha256="0" * 64))

    with pytest.raises(CommunityMismatch, match="community graph"):
        community_records(graph_test_batch, stale)


def test_reports_written_for_another_community_graph_are_refused(graph_test_batch, inputs):
    stale = replace(inputs, reports=replace(inputs.reports,
                                            community_graph_input_sha256="0" * 64))

    with pytest.raises(CommunityMismatch, match="reports"):
        community_records(graph_test_batch, stale)


def test_reports_written_for_another_communities_file_are_refused(graph_test_batch, inputs):
    stale = replace(inputs, reports=replace(inputs.reports, communities_sha256="0" * 64))

    with pytest.raises(CommunityMismatch, match="communities-run1.json"):
        community_records(graph_test_batch, stale)


def test_an_incomplete_reports_file_is_refused(graph_test_batch, inputs):
    incomplete = replace(inputs, reports=replace(inputs.reports, complete=False))

    with pytest.raises(CommunityMismatch, match="complete"):
        community_records(graph_test_batch, incomplete)


def test_a_member_missing_from_the_graph_is_refused(graph_test_batch, inputs):
    extra = replace(COMMUNITIES[2], members=(*COMMUNITIES[2].members, "org:nobody"))
    inputs = replace(inputs, communities=replace(
        inputs.communities, communities=(*COMMUNITIES[:2], extra)))

    with pytest.raises(CommunityMismatch, match="org:nobody"):
        community_records(graph_test_batch, inputs)


# ── reading the reports file ──────────────────────────────────────────────────

def test_the_committed_reports_file_reads_back():
    found = read_reports_file(latest_reports_file(REPO_ROOT / "benchmarks" / "communities"))

    assert found.complete
    assert found.communities_file.startswith("communities-run")
    assert all(e.prompt_version.startswith("report/") for e in found.entries.values())


def _write(path, lines):
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return path


HEADER = {"kind": "header", "complete": True,
          "communities": {"file": "communities-run1.json", "sha256": "c" * 64,
                          "community_graph_input_sha256": "a" * 64}}
REPORT = {"kind": "report", "community_id": "lower-x", "member_hash": "b" * 64,
          "level": "lower", "title": "T", "report": "R [c:0001].",
          "report_chunk_ids": ["c:0001"], "prompt_version": TEST_REPORT_PROMPT}


def test_a_reports_file_reads_its_header_and_reports(tmp_path):
    found = read_reports_file(_write(tmp_path / "reports-run1.jsonl", [HEADER, REPORT]))

    assert found.entries["lower-x"].report_chunk_ids == ("c:0001",)
    assert found.community_graph_input_sha256 == "a" * 64


@pytest.mark.parametrize("lines", [
    [REPORT],
    [HEADER, {**REPORT, "level": "middle"}],
    [HEADER, {**REPORT, "report_chunk_ids": "c:0001"}],
    [HEADER, REPORT, REPORT],
    [{**HEADER, "complete": "yes"}, REPORT],
], ids=["no header", "bad level", "chunk ids not a list", "two reports", "complete not bool"])
def test_a_malformed_reports_file_is_refused(tmp_path, lines):
    with pytest.raises(ReportsFileError):
        read_reports_file(_write(tmp_path / "reports-run1.jsonl", lines))
