"""The Neo4j driver, built from environment variables (Step 7).

Community has one standard database, so every query names it explicitly
(`database_=DATABASE`), which also saves the driver a round trip. Telemetry to
the server is off.
"""
from __future__ import annotations

import os

from neo4j import READ_ACCESS, Driver, GraphDatabase, Session

DATABASE = "neo4j"


class GraphSettingsMissing(RuntimeError):
    """A connection setting is unset; the message names each one."""


def driver_from_env(prefix: str = "NEO4J") -> Driver:
    """A driver from {prefix}_URI, {prefix}_USER and {prefix}_PASSWORD.

    Graph tests pass prefix "NEO4J_TEST", so they never read the real graph's settings.
    """
    names = [f"{prefix}_{suffix}" for suffix in ("URI", "USER", "PASSWORD")]
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise GraphSettingsMissing(f"Neo4j settings not set: {', '.join(missing)}")
    uri, user, password = (os.environ[name] for name in names)
    return GraphDatabase.driver(uri, auth=(user, password), telemetry_disabled=True)


def read_session(driver: Driver, database: str = DATABASE, **config: object) -> Session:
    """A session in READ access mode, which makes the server refuse writes.

    Community has no read-only users, and READ mode does not stop LOAD CSV,
    TERMINATE TRANSACTIONS or dbms procedures (ADR-0007), so generated queries
    also pass the guard's text rules first. *config* goes to driver.session().
    """
    return driver.session(database=database, default_access_mode=READ_ACCESS, **config)
