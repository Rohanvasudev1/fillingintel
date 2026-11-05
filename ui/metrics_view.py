"""An answer's metrics (Step 14).

A sample shows its committed per-question record from the option's dev run,
labelled as the benchmark labels it.  Every answer shows the numbers that need
no gold: sentences kept and dropped, citations inside the retrieved chunks,
and cost and latency per stage.  No judge is called.
"""
from __future__ import annotations

import streamlit as st

from ui.backend import Backend, UiAnswer
from ui.committed import SampleMetrics
from ui.errors import BackendError
from ui.render import markdown_dollars, md_escape

SAMPLE_LABEL = "agent-drafted questions, judged uncalibrated"


def show_metrics(backend: Backend, answer: UiAnswer) -> None:
    st.markdown("#### Metrics")
    if answer.sample_id is not None:
        _sample_metrics(backend, answer.option, answer.sample_id)
    check = answer.result.answer.citation_check
    st.markdown(f"Citations inside the retrieved chunks: {check.citations_retrieved} of "
                f"{check.citations}")
    st.dataframe(stage_rows(answer), hide_index=True)
    replay = "replayed from the response cache" if answer.replayed else "made live"
    st.caption(markdown_dollars(
        f"Cost and latency per stage for this answer; calls {replay}. A replayed call shows "
        f"the latency it had when it was made. This answer added ${answer.spent_usd:.4f} to "
        "the session."))


def _sample_metrics(backend: Backend, option: str, sample_id: str) -> None:
    try:
        metrics = backend.sample_metrics(option, sample_id)
    except BackendError as exc:
        st.error(md_escape(str(exc)))
        return
    if metrics is None:
        return
    st.dataframe(sample_rows(metrics), hide_index=True)
    judged = "" if metrics.judged_label else " Retrieval only: this run wrote no answer."
    st.caption(f"Committed dev metrics for {sample_id} ({metrics.class_}), from "
               f"`{metrics.run_file}`: {SAMPLE_LABEL}.{judged}")


def sample_rows(metrics: SampleMetrics) -> list[dict[str, str]]:
    rows = [{"metric": name, "value": f"{value:.3f}"}
            for name, value in metrics.retrieval.items()]
    if metrics.wrong_evidence is not None:
        rows.append({"metric": "wrong evidence", "value": "yes" if metrics.wrong_evidence
                     else "no"})
    label = metrics.judged_label or "uncalibrated"
    rows += [{"metric": f"{name} ({label})", "value": f"{value:.3f}"}
             for name, value in metrics.judged.items()]
    return rows


def stage_rows(answer: UiAnswer) -> list[dict[str, str]]:
    """Latency, cost and whether it replayed, per stage, then the total."""
    result = answer.result
    rows = [_row("embedding", result.embed_ms, result.embed_cost_usd, result.query_cached)]
    if result.graph is None:  # a graph arm's search time is its stages
        rows.append(_row("search", result.search_ms, 0.0, None))
    else:
        rows += [_row(name, stage.ms, stage.cost_usd, stage.from_cache)
                 for name, stage in result.graph.stages.items()]
    if result.routing is not None:
        rows.append(_row("routing", result.routing.cost.ms, result.routing.cost.cost_usd,
                         result.routing.cost.from_cache))
    rows.append(_row("generation", result.answer.generation_ms, result.answer.cost_usd,
                     result.answer.from_cache))
    rows.append(_row("total", answer.total_ms, answer.cost["total"], answer.replayed))
    return rows


def _row(stage: str, ms: float, usd: float, replayed: bool | None) -> dict[str, str]:
    return {"stage": stage, "ms": f"{ms:,.0f}", "USD": f"{usd:.4f}",
            "replayed": "" if replayed is None else "yes" if replayed else "no"}
