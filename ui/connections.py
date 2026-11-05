"""The connections the UI backend reads through beside the arms (Step 14).

One Postgres connection for source passages and one Neo4j driver for community
titles, each opened on first use, shared across sessions under a lock, and
reopened once after a lost connection.  ``open_backend`` wires them into a
``LiveBackend``.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable, Collection, Mapping

import psycopg
from neo4j import Driver
from neo4j.exceptions import DriverError, Neo4jError

from graph.connection import GraphSettingsMissing, driver_from_env
from ui.backend import LiveBackend
from ui.communities import CommunityInfo, read_communities
from ui.errors import NEO4J_DOWN, POSTGRES_DOWN, DatabaseUnavailable, neo4j_settings_missing
from ui.source import SourcePassage, read_source


class SharedConnections:
    """Source passages from Postgres and community titles from Neo4j."""

    def __init__(self, connect: Callable[[], psycopg.Connection],
                 driver: Callable[[], Driver]):
        self._connect = connect
        self._make_driver = driver
        self._conn: psycopg.Connection | None = None
        self._driver: Driver | None = None
        self._lock = threading.Lock()

    def source(self, chunk_id: str) -> SourcePassage:
        with self._lock:
            for attempt in (1, 2):
                try:
                    return read_source(self._postgres(), chunk_id)
                except psycopg.OperationalError as exc:
                    self._close_postgres()
                    if attempt == 2:
                        raise DatabaseUnavailable(POSTGRES_DOWN) from exc
        raise AssertionError("unreachable")

    def communities(self, community_ids: Collection[str]) -> Mapping[str, CommunityInfo]:
        with self._lock:
            if self._driver is None:
                try:
                    self._driver = self._make_driver()
                except GraphSettingsMissing as exc:
                    raise neo4j_settings_missing(exc) from exc
            try:
                return read_communities(self._driver, community_ids)
            except (Neo4jError, DriverError) as exc:
                self._driver.close()
                self._driver = None
                raise DatabaseUnavailable(NEO4J_DOWN) from exc

    def close(self) -> None:
        with self._lock:
            self._close_postgres()
            if self._driver is not None:
                self._driver.close()
                self._driver = None

    def _postgres(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            try:
                self._conn = self._connect()
            except psycopg.Error as exc:  # the message can quote the URL: never shown
                raise DatabaseUnavailable(POSTGRES_DOWN) from exc
        return self._conn

    def _close_postgres(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def open_backend(environ: Mapping[str, str] | None = None,
                 ) -> tuple[LiveBackend, Callable[[], None]]:
    """The live backend on the environment's databases, and a function that closes it."""
    environ = dict(os.environ if environ is None else environ)
    shared = SharedConnections(
        lambda: psycopg.connect(environ.get("DATABASE_URL", ""), autocommit=True,
                                connect_timeout=3),
        driver_from_env)
    backend = LiveBackend(environ=environ, communities=shared.communities,
                          source_reader=shared.source)

    def close() -> None:
        backend.close()
        shared.close()

    return backend, close
