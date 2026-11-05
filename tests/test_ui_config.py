"""The committed Streamlit config keeps the app local and quiet (Step 14)."""
from __future__ import annotations

import tomllib

from ingest.provenance import REPO_ROOT

CONFIG = REPO_ROOT / ".streamlit" / "config.toml"


def test_config_turns_telemetry_off_runs_headless_and_binds_to_localhost():
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))

    assert config["browser"]["gatherUsageStats"] is False
    assert config["server"]["headless"] is True
    assert config["server"]["address"] == "127.0.0.1"
