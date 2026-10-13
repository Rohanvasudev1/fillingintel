"""Exit codes and the stop signal shared by the persistence commands (Step 10)."""
from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 2  # bad arguments or a malformed file
EXIT_RUN_ERROR = 3  # could not run
EXIT_CHECK_FAILED = 4  # a check failed or the run is incomplete


class Stop(Exception):
    """The command stops with *code*; the message is safe to print."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
