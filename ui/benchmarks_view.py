"""The Benchmarks tab: the committed results.md, as CI checks it (Step 14).

Nothing is recomputed.  Streamlit's Markdown escapes HTML; ``$`` is escaped too,
so two prices on one line are not read as a formula.
"""
from __future__ import annotations

import streamlit as st

from ingest.provenance import REPO_ROOT
from ui.render import markdown_dollars

RESULTS = REPO_ROOT / "benchmarks" / "results.md"
HEADER = ("Every number below is on **agent-drafted questions** (140 questions agents wrote, "
          "not reviewed by a human). Judged columns (faithfulness, relevancy, citation "
          "support) are **uncalibrated**. The `test` tables are the benchmark; the `dev` "
          "tables come last and are **tuning data**.")


def show_benchmarks() -> None:
    st.markdown(HEADER)
    st.caption("`benchmarks/results.md`, built by `python -m eval.results_table` from the "
               "committed run files and checked in CI.")
    try:
        text = RESULTS.read_text(encoding="utf-8")
    except OSError as exc:
        st.error(f"benchmarks/results.md cannot be read ({type(exc).__name__}).")
        return
    st.markdown(markdown_dollars(text))
