"""graph_global's selected communities for the answer trace (Step 14).

Titles and summaries are read from ``:Community`` nodes by ``community_id`` when
the trace is built; they are never stored on the arm's result, and never cited.
A report is its summary, then one ``- `` line per finding (graph.report_check).
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from neo4j import Driver

from graph.connection import DATABASE, read_session
from graph.report_check import FINDING_PREFIX

_COMMUNITIES = ("MATCH (c:Community) WHERE c.community_id IN $ids "
                "RETURN c.community_id AS id, c.title AS title, c.report AS report")


@dataclass(frozen=True)
class CommunityInfo:
    community_id: str
    title: str | None  # None for a community with no report
    summary: str | None


def read_communities(driver: Driver, community_ids: Collection[str],
                     database: str = DATABASE) -> Mapping[str, CommunityInfo]:
    """Each of *community_ids* the graph holds; raises ``Neo4jError`` or ``DriverError``."""
    if not community_ids:
        return {}
    with read_session(driver, database) as session:
        rows = session.execute_read(
            lambda tx: [r.data() for r in tx.run(_COMMUNITIES, ids=sorted(community_ids))])
    return {row["id"]: CommunityInfo(row["id"], row["title"], summary_of(row["report"]))
            for row in rows}


def summary_of(report: str | None) -> str | None:
    """The report's summary line; None when it has none."""
    if not report:
        return None
    first = report.split("\n", 1)[0].strip()
    return None if not first or first.startswith(FINDING_PREFIX.strip()) else first
