"""The Cypher guard and executor for generated queries (Step 11, ADR-0007).

`run_guarded()` takes a query string and returns either its rows or the first
check it failed, with a message a repair prompt can use. The checks run in order:
1. text rules (graph/cypher_text.py): one statement; no LOAD CSV, TERMINATE,
   SHOW, USE, write clause or procedure CALL;
2. the ontology check (graph/cypher_ontology.py): names and endpoint direction;
3. EXPLAIN in a READ session: query type `r` and no unknown label, edge type or
   property notification (01N50, 01N51, 01N52);
4. execution in a READ transaction with a timeout, through an explicit
   transaction so the driver never retries, reading at most MAX_ROWS rows. The
   result needs a `chunk_ids` column holding lists of strings.
The fifth guard, the server-wide timeout and memory limits, is set in
docker-compose.yml.

Query failures come back as GuardFailure with a category: `invalid` (the query
breaks a rule, fails to parse or the server refuses it as a write), `timeout`
or `error` (anything else the server reports, such as the memory limit).
A lost connection is not a query failure and is raised as the driver's error.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import islice

from neo4j import Driver
from neo4j.exceptions import CypherSyntaxError, Neo4jError

from graph.connection import DATABASE, read_session
from graph.cypher_ontology import ontology_violation
from graph.cypher_text import CypherTextError, text_violation, tokenize

CHUNK_IDS = "chunk_ids"
TIMEOUT_S = 10.0
MAX_ROWS = 1_000
UNKNOWN_NAME_STATUSES = frozenset({"01N50", "01N51", "01N52"})
_TIMEOUT_CODE = "TransactionTimedOut"  # also matches ...TimedOutClientConfiguration
_ACCESS_MODE_CODE = "Neo.ClientError.Statement.AccessMode"


class Category(StrEnum):
    INVALID = "invalid"
    ERROR = "error"
    TIMEOUT = "timeout"


class Rule(StrEnum):
    """The check a query failed."""

    TEXT = "text"
    ONTOLOGY = "ontology"
    EXPLAIN = "explain"
    EXECUTE = "execute"
    RESULT = "result"  # ran, but without a chunk_ids column of string lists


@dataclass(frozen=True, slots=True)
class GuardFailure:
    category: Category
    rule: Rule
    message: str  # written for the repair prompt


@dataclass(frozen=True, slots=True)
class GuardPass:
    rows: tuple[dict[str, object], ...]
    truncated: bool  # more than MAX_ROWS rows; only the first MAX_ROWS were read


GuardResult = GuardPass | GuardFailure


def static_check(cypher: str) -> GuardFailure | None:
    """Checks 1 and 2, which need no database: the first failure, or None."""
    try:
        tokens = tokenize(cypher)
    except CypherTextError as exc:
        return GuardFailure(Category.INVALID, Rule.TEXT, str(exc))
    if not tokens:
        return GuardFailure(Category.INVALID, Rule.TEXT, "the query is empty")
    if (message := text_violation(tokens)) is not None:
        return GuardFailure(Category.INVALID, Rule.TEXT, message)
    if (message := ontology_violation(tokens)) is not None:
        return GuardFailure(Category.INVALID, Rule.ONTOLOGY, message)
    return None


def _from_error(exc: Neo4jError, rule: Rule) -> GuardFailure:
    code = exc.code or ""
    message = f"{code}: {exc.message}" if code else str(exc)
    if _TIMEOUT_CODE in code:
        return GuardFailure(Category.TIMEOUT, rule, message)
    if isinstance(exc, CypherSyntaxError) or code == _ACCESS_MODE_CODE:
        return GuardFailure(Category.INVALID, rule, message)
    return GuardFailure(Category.ERROR, rule, message)


def explain_check(driver: Driver, cypher: str, *, database: str = DATABASE,
                  timeout: float = TIMEOUT_S) -> GuardFailure | None:
    """Check 3: EXPLAIN in a READ transaction must plan a read with no unknown names."""
    try:
        with read_session(driver, database) as session, \
                session.begin_transaction(timeout=timeout) as tx:
            summary = tx.run(f"EXPLAIN {cypher}").consume()
    except Neo4jError as exc:
        return _from_error(exc, Rule.EXPLAIN)
    if summary.query_type != "r":
        return GuardFailure(Category.INVALID, Rule.EXPLAIN,
                            f"the query plans as type {summary.query_type!r}, not a read ('r')")
    unknown = [s for s in summary.gql_status_objects if s.gql_status in UNKNOWN_NAME_STATUSES]
    if unknown:
        return GuardFailure(Category.INVALID, Rule.EXPLAIN, "; ".join(
            f"{s.gql_status}: {s.status_description}" for s in unknown))
    return None


def execute(driver: Driver, cypher: str, *, database: str = DATABASE,
            timeout: float = TIMEOUT_S, max_rows: int = MAX_ROWS) -> GuardResult:
    """Check 4: the rows of *cypher*, read in a READ transaction, at most *max_rows*.

    The transaction is explicit, so the driver does not retry a transient error,
    and it is rolled back when read. Reading stops after *max_rows*; the server
    stops at its own timeout.
    """
    try:
        with read_session(driver, database, fetch_size=max_rows + 1) as session, \
                session.begin_transaction(timeout=timeout) as tx:
            result = tx.run(cypher)
            keys = result.keys()
            rows = [record.data() for record in islice(result, max_rows + 1)]
    except Neo4jError as exc:
        return _from_error(exc, Rule.EXECUTE)
    if CHUNK_IDS not in keys:
        return GuardFailure(Category.INVALID, Rule.RESULT,
                            f"return a column named {CHUNK_IDS} holding a list of chunk IDs; "
                            f"the query returned {list(keys)}")
    bad = [r[CHUNK_IDS] for r in rows if not _is_string_list(r[CHUNK_IDS])]
    if bad:
        return GuardFailure(Category.INVALID, Rule.RESULT,
                            f"{CHUNK_IDS} must be a list of strings in every row; got {bad[0]!r}")
    return GuardPass(tuple(rows[:max_rows]), truncated=len(rows) > max_rows)


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def run_guarded(driver: Driver, cypher: str, *, database: str = DATABASE,
                timeout: float = TIMEOUT_S, max_rows: int = MAX_ROWS) -> GuardResult:
    """All four checks in order: the rows, or the first failure."""
    if (failure := static_check(cypher)) is not None:
        return failure
    if (failure := explain_check(driver, cypher, database=database,
                                 timeout=timeout)) is not None:
        return failure
    return execute(driver, cypher, database=database, timeout=timeout, max_rows=max_rows)
