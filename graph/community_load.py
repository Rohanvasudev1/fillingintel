"""Communities and their reports as graph records, for the rebuild (Step 12).

`graph.load` reads the latest communities file and the latest reports file
(both committed under `benchmarks/communities/`), and community_records()
turns them into Community nodes, IN_COMMUNITY and PARENT_OF edges, added to
the builder's batch so the rebuild writes them in its one transaction.

community_records() is pure. It refuses with CommunityMismatch, before
anything is written, when the files do not fit the graph being built:

- the communities file was made from another community graph (its input hash
  differs from community_graph() over the builder's batch);
- the reports file is incomplete, or was written for another communities file
  or community graph;
- a reportable community has no report under its community_id, or the report
  carries another member hash;
- a member key is not an extracted non-metric node of the batch.

A report is matched by `community_id`, then checked by `member_hash`: a top
community that is its own lower one shares its child's member hash. A
community too small for a report, or whose report kept no text, is written
with no title, report or report prompt and an empty `report_chunk_ids`.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from graph.batch import Batch, EdgeRecord, NodeRecord, NodeRef
from graph.community import LOWER, TOP, Community
from graph.community_file import (
    CommunitiesFile,
    latest_communities_file,
    read_communities_file,
)
from graph.community_graph import KEY, community_graph
from graph.ontology import (
    COMMUNITY,
    COMMUNITY_MEMBER_LABELS,
    IN_COMMUNITY,
    LABELS_BY_NAME,
    PARENT_OF,
)
from graph.report_file import ReportEntry, ReportsFile, latest_reports_file, read_reports_file

(COMMUNITY_ID,) = LABELS_BY_NAME[COMMUNITY].keys


class CommunityMismatch(ValueError):
    """The communities or reports file does not fit the graph being built; nothing is
    written."""


@dataclass(frozen=True, slots=True)
class CommunityInputs:
    communities: CommunitiesFile
    reports: ReportsFile


@dataclass(frozen=True, slots=True)
class CommunityRecords:
    nodes: tuple[NodeRecord, ...]
    edges: tuple[EdgeRecord, ...]
    counts: Mapping[str, int]
    sources: Mapping[str, str]  # the two files' names and SHA-256s, for the load report


def read_community_inputs(directory: Path) -> CommunityInputs:
    """The latest communities and reports files in *directory*.

    CommunitiesFileError or ReportsFileError when one is missing or malformed.
    """
    return CommunityInputs(read_communities_file(latest_communities_file(directory)),
                           read_reports_file(latest_reports_file(directory)))


def community_ref(community_id: str) -> NodeRef:
    return NodeRef.of(COMMUNITY, **{COMMUNITY_ID: community_id})


def community_records(batch: Batch, inputs: CommunityInputs) -> CommunityRecords:
    """The Community records for *batch*; CommunityMismatch when the files do not fit it."""
    _check_files(batch, inputs)
    members = _member_labels(batch)
    reports = inputs.reports.entries
    nodes: list[NodeRecord] = []
    edges: list[EdgeRecord] = []
    for community in inputs.communities.communities:
        report = _report(community, reports) if community.reportable else None
        nodes.append(NodeRecord(COMMUNITY, _properties(community, report)))
        edges += _edges(community, members)
    sources = {"communities_file": inputs.communities.name,
               "communities_sha256": inputs.communities.sha256,
               "reports_file": inputs.reports.name, "reports_sha256": inputs.reports.sha256}
    return CommunityRecords(tuple(nodes), tuple(edges), _counts(nodes, edges), sources)


def _check_files(batch: Batch, inputs: CommunityInputs) -> None:
    communities, reports = inputs.communities, inputs.reports
    built = community_graph(batch).input_sha256
    if communities.input_sha256 != built:
        raise CommunityMismatch(
            f"{communities.name} was made from another community graph "
            f"({communities.input_sha256[:12]}, the graph being built gives {built[:12]}); "
            "run python -m graph.communities")
    if not reports.complete:
        raise CommunityMismatch(f"{reports.name} is not complete; rerun python -m graph.reports")
    if reports.communities_sha256 != communities.sha256:
        raise CommunityMismatch(
            f"{reports.name} was written for {reports.communities_file} "
            f"({reports.communities_sha256[:12]}), not {communities.name} "
            f"({communities.sha256[:12]}); run python -m graph.reports")
    if reports.community_graph_input_sha256 != built:
        raise CommunityMismatch(
            f"{reports.name} reports were written for another community graph "
            f"({reports.community_graph_input_sha256[:12]}); run python -m graph.reports")


def _member_labels(batch: Batch) -> dict[str, str]:
    """key -> label for every node a community may hold."""
    return {str(n.properties[KEY]): n.label for n in batch.nodes
            if n.label in COMMUNITY_MEMBER_LABELS}


def _report(community: Community, reports: Mapping[str, ReportEntry]) -> ReportEntry:
    report = reports.get(community.community_id)
    if report is None:
        raise CommunityMismatch(f"reportable community {community.community_id} has no report; "
                                "run python -m graph.reports")
    if report.member_hash != community.member_hash or report.level != community.level:
        raise CommunityMismatch(
            f"the report for {community.community_id} was written for another member hash "
            f"or level; run python -m graph.reports")
    return report


def _properties(community: Community, report: ReportEntry | None) -> dict[str, object]:
    props: dict[str, object] = {
        COMMUNITY_ID: community.community_id, "level": community.level,
        "member_hash": community.member_hash, "report_chunk_ids": [],
    }
    if report is not None and report.report:
        props |= {"title": report.title, "report": report.report,
                  "report_chunk_ids": list(report.report_chunk_ids),
                  "report_prompt": report.prompt_version}
    return props


def _edges(community: Community, members: Mapping[str, str]) -> Iterator[EdgeRecord]:
    ref = community_ref(community.community_id)
    if community.level == TOP:
        return
    if community.parent is not None:
        yield EdgeRecord(PARENT_OF, community_ref(community.parent), ref)
    for key in community.members:
        label = members.get(key)
        if label is None:
            raise CommunityMismatch(f"{community.community_id} holds {key!r}, which is not a "
                                    "non-metric extracted node of the graph being built")
        yield EdgeRecord(IN_COMMUNITY, NodeRef.of(label, **{KEY: key}), ref)


def _counts(nodes: list[NodeRecord], edges: list[EdgeRecord]) -> dict[str, int]:
    levels = [n.properties["level"] for n in nodes]
    reported = sum("report" in n.properties for n in nodes)
    return {"top": levels.count(TOP), "lower": levels.count(LOWER), "with_report": reported,
            "without_report": len(nodes) - reported,
            "members": sum(e.type == IN_COMMUNITY for e in edges)}
