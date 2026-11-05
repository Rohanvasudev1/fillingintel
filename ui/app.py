"""Start the FilingIntel app (Step 14):

    docker compose up -d
    uv run --env-file .env streamlit run ui/app.py

Serves http://127.0.0.1:8501 (``.streamlit/config.toml``).  The live backend,
its arms and its database connections are opened once and shared by every
browser session; ``ui.page.main`` draws the page.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # `streamlit run` puts only ui/ on the path
    sys.path.insert(0, str(REPO))
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(REPO / "vendor" / "tiktoken"))

import streamlit as st  # noqa: E402

from retrieve.tracing import TracingConfigError  # noqa: E402
from ui.backend import LiveBackend  # noqa: E402
from ui.connections import open_backend  # noqa: E402
from ui.errors import BackendError  # noqa: E402
from ui.page import main  # noqa: E402
from ui.render import md_escape  # noqa: E402


class _Opened(NamedTuple):
    backend: LiveBackend
    close: Callable[[], None]


def _close(opened: _Opened) -> None:
    opened.close()


@st.cache_resource(show_spinner="Opening the backend...", on_release=_close)
def _live_backend() -> _Opened:
    return _Opened(*open_backend())


try:
    backend = _live_backend().backend
except (BackendError, TracingConfigError) as exc:  # a bad setting: fix it and restart
    st.error(md_escape(str(exc)))
    st.stop()
main(backend)
