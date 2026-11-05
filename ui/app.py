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
from pathlib import Path

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
from ui.render import markdown_dollars  # noqa: E402


def _close(opened: tuple[LiveBackend, object]) -> None:
    opened[1]()


@st.cache_resource(show_spinner="Opening the backend...", on_release=_close)
def _live_backend() -> tuple[LiveBackend, object]:
    return open_backend()


try:
    backend = _live_backend()[0]
except (BackendError, TracingConfigError) as exc:  # a bad setting: fix it and restart
    st.error(markdown_dollars(str(exc)))
    st.stop()
main(backend)
