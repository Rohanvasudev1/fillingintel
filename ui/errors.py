"""The UI backend's errors (Step 14).

Every error the backend raises is a ``BackendError`` whose message is plain
words the app shows as they are: what is wrong and what to do.  None carries a
traceback, a key or a connection string.
"""
from __future__ import annotations

POSTGRES_DOWN = ("Postgres is not reachable at DATABASE_URL. Start it with docker compose up -d, "
                 "then try again.")
NEO4J_DOWN = ("Neo4j is not reachable at NEO4J_URI. Start it with docker compose up -d, then "
              "try again.")


class BackendError(RuntimeError):
    """Something the reader can act on; the message is safe to show."""


class UnknownOption(BackendError, ValueError):
    """A picker option the UI does not offer."""


class SettingsMissing(BackendError):
    """An environment variable an option needs is not set."""


class UnpricedModel(BackendError):
    """An option's model has no price, so its cost could not be shown."""


class DatabaseUnavailable(BackendError):
    """Postgres or Neo4j could not be reached."""


class AnswerFailed(BackendError):
    """The arm could not answer; the message is the arm's own."""


class SpendCapReached(BackendError):
    """A live question would pass the session's spend cap."""


class SpendConfigError(BackendError, ValueError):
    """The spend cap setting is not a usable amount."""


class SourceNotFound(BackendError, LookupError):
    """No stored chunk has this ID."""


class SamplesFileError(BackendError, ValueError):
    """The samples file is missing or malformed."""


class CommittedRunError(BackendError, ValueError):
    """A committed run file cannot be read."""


def neo4j_settings_missing(exc: Exception) -> SettingsMissing:
    """The plain error for ``graph.connection.GraphSettingsMissing``."""
    return SettingsMissing(f"{exc}; set it in .env and restart the app.")
