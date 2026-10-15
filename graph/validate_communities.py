"""The community checks of validate_graph() (Step 12, ontology version 5).

The generic checks already cover a Community's properties and its edges'
endpoints, so a metric value or a chunk can never be a member. These add the
rules that make the two levels a tree whose reports rest on member evidence
(ADR-0004 amendment of 2026-10-15):

- members join lower communities only, and each member joins one;
- PARENT_OF runs from a top community to a lower one;
- each lower community has exactly one parent, and each top one a child;
- the member hash, recomputed from the members (a top community's are its
  children's), equals `member_hash`;
- every `report_chunk_ids` entry is an EVIDENCED_BY chunk of a member.

Labels and edge types come from the ontology through graph.cypher.quoted();
levels are parameters.
"""
from __future__ import annotations

from collections.abc import Iterator

from neo4j import ManagedTransaction

from graph.batch import CHUNK, CHUNK_ID, EVIDENCED_BY
from graph.community import LOWER, TOP, member_hash
from graph.cypher import quoted
from graph.ontology import COMMUNITY, IN_COMMUNITY, PARENT_OF
from graph.validation_report import Check, Finding

_IN, _PARENT = quoted(IN_COMMUNITY), quoted(PARENT_OF)
_C, _CHUNK, _EVIDENCE = quoted(COMMUNITY), quoted(CHUNK), quoted(EVIDENCED_BY)

# Per community, its members' keys and their evidence chunks: a lower community's
# own members, a top community's through its children.
_MEMBER_PATHS = {
    LOWER: f"(c)<-[:{_IN}]-(m)",
    TOP: f"(c)-[:{_PARENT}]->(:{_C})<-[:{_IN}]-(m)",
}


def community_findings(tx: ManagedTransaction) -> Iterator[Finding]:
    """Every community finding, check COMMUNITIES."""
    for message in (*_membership(tx), *_parents(tx), *_members_and_reports(tx)):
        yield Finding(Check.COMMUNITIES, message)


def _membership(tx: ManagedTransaction) -> Iterator[str]:
    result = tx.run(
        f"MATCH (m)-[:{_IN}]->(c:{_C}) WHERE c.level <> $lower "
        "RETURN labels(m) AS labels, m.key AS key, c.community_id AS id, c.level AS level",
        lower=LOWER,
    )
    for r in result:
        yield (f"{':'.join(r['labels'])}({r['key']!r}) is in {r['id']!r}, a {r['level']} "
               "community; members join lower communities")
    result = tx.run(
        f"MATCH (m)-[:{_IN}]->(c:{_C}) WITH m, collect(c.community_id) AS ids "
        "WHERE size(ids) > 1 RETURN labels(m) AS labels, m.key AS key, ids",
    )
    for r in result:
        yield (f"{':'.join(r['labels'])}({r['key']!r}) is in {len(r['ids'])} communities "
               f"{', '.join(map(repr, sorted(r['ids'])))}; expected one")


def _parents(tx: ManagedTransaction) -> Iterator[str]:
    result = tx.run(
        f"MATCH (a:{_C})-[:{_PARENT}]->(b:{_C}) WHERE a.level <> $top OR b.level <> $lower "
        "RETURN a.community_id AS a, a.level AS a_level, b.community_id AS b, "
        "b.level AS b_level",
        top=TOP, lower=LOWER,
    )
    for r in result:
        yield (f"{r['a']!r} ({r['a_level']}) is PARENT_OF {r['b']!r} ({r['b_level']}); "
               "PARENT_OF runs from a top community to a lower one")
    result = tx.run(
        f"MATCH (c:{_C}) WHERE c.level = $lower "
        f"WITH c, COUNT {{ (:{_C})-[:{_PARENT}]->(c) }} AS parents WHERE parents <> 1 "
        "RETURN c.community_id AS id, parents",
        lower=LOWER,
    )
    for r in result:
        yield f"lower community {r['id']!r} has {r['parents']} parents; expected one"
    result = tx.run(
        f"MATCH (c:{_C}) WHERE c.level = $top AND NOT EXISTS {{ (c)-[:{_PARENT}]->(:{_C}) }} "
        "RETURN c.community_id AS id",
        top=TOP,
    )
    for r in result:
        yield f"top community {r['id']!r} has no lower community"


def _members_and_reports(tx: ManagedTransaction) -> Iterator[str]:
    for level, path in _MEMBER_PATHS.items():
        result = tx.run(  # pattern comprehensions: one bounded expansion per community
            f"MATCH (c:{_C}) WHERE c.level = $level "
            "RETURN c.community_id AS id, c.member_hash AS hash, c.report_chunk_ids AS cited, "
            f"[{path} | m.key] AS members, "
            f"[{path}-[:{_EVIDENCE}]->(k:{_CHUNK}) | k.{quoted(CHUNK_ID)}] AS evidence",
            level=level,
        )
        for r in result:
            yield from _community_problems(level, r["id"], r["hash"], r["cited"],
                                           r["members"], frozenset(r["evidence"]))


def _community_problems(
    level: str, community_id: object, stored_hash: object, cited: object,
    members: list[object], evidence: frozenset[object],
) -> Iterator[str]:
    """What is wrong with one community, as read back: its stored hash and citations
    against its members' keys and evidence chunks."""
    keys = {k for k in members if isinstance(k, str)}
    # The hash is only recomputed when every member has a text key; a member without
    # one is reported by the property checks, not as a hash mismatch here.
    if all(isinstance(k, str) for k in members) and member_hash(keys) != stored_hash:
        yield (f"{level} community {community_id!r}: member_hash does not match its "
               f"{len(keys)} members")
    if isinstance(cited, list):  # otherwise reported by the property checks
        if outside := sorted(c for c in cited if c not in evidence):
            whose = "its members'" if level == LOWER else "its lower communities' members'"
            yield (f"{level} community {community_id!r}: report cites "
                   f"{', '.join(map(repr, outside))}, not {whose} evidence")
