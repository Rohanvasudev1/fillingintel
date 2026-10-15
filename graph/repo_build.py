"""The builder's output from the repo's inputs, for the commands that read it (Step 12).

read_built() reads what `graph.load` reads (Postgres, the latest extract
files, the alias map and the persistence links) and runs the same pure
builder. `graph.communities` and `graph.reports` both start from it.
"""
from __future__ import annotations

import os

import psycopg

from graph.build_input import InputError
from graph.builder import BuildError, Built, build_graph
from graph.load import LoadInputs, read_repo_inputs
from graph.structural import read_structural

DATABASE_ENV = "DATABASE_URL"


class CouldNotRun(RuntimeError):
    """The inputs could not be read or built; the message is safe to print."""


def read_built() -> tuple[LoadInputs, Built]:
    """The load inputs and the builder's output, as `graph.load` makes them."""
    url = os.environ.get(DATABASE_ENV)
    if not url:
        raise CouldNotRun(f"{DATABASE_ENV} is not set")
    try:
        inputs = read_repo_inputs()
        with psycopg.connect(url) as conn:
            conn.read_only = True
            rows = read_structural(conn)
        return inputs, build_graph(rows, inputs.files, inputs.alias_map, inputs.links)
    except (InputError, BuildError, psycopg.Error) as exc:
        raise CouldNotRun(f"{type(exc).__name__}: {exc}") from exc
