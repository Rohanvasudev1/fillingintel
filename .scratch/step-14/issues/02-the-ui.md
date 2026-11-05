# 02: The UI

**What to build:** `uv run streamlit run ui/app.py` opens a local app with two tabs. **Ask:** choose an option (default `vector`), click a sample chip or pick from the list or type a question, and see the answer with numbered citations, a chip per cited chunk that shows the chunk highlighted in its filing text, the answer trace, and the metrics. Live questions show their cost estimate first and are refused past the session cap. **Benchmarks:** the committed `results.md`. Ends with a reproducible screenshot committed for the README. Spec: `.scratch/step-14/spec.md` (Ask view; Benchmarks tab; Dependencies and config; Screenshot). Research: docs/research/streamlit-ui.md. Vocabulary: CONTEXT.md (sample question, live question, answer trace).

**Blocked by:** 01 (Backend, trace fields and samples)

**Status:** ready-for-agent

**Parallel sessions:** as ticket 01. Step 14 spend is capped at $2; the screenshot and manual checks use samples with dummy keys, so no paid call is expected. Ask before any live question.

- [ ] `streamlit==1.66.0` added as a main dependency, pinned exactly; `.streamlit/config.toml` committed with `browser.gatherUsageStats = false`, `server.headless = true`, `server.address = "127.0.0.1"`; a test reads it.
- [ ] The app takes a `Backend`, real by default; AppTest tests pass the fake from ticket 01 and run offline in CI.
- [ ] Ask view: picker with `vector` default and the notes ("best measured arm", "tuned on dev"); six chips and a selectbox from the samples file for the chosen option; the question box in a form; a status element while an answer runs; the answer kept in session state across reruns.
- [ ] Live questions: the estimate shown before sending; a refusal message when the cap is reached; the session's spend shown.
- [ ] Answer: kept sentences escaped, with numbered markers; dropped sentences counted; decline and not-found show their fixed sentences. One `st.pills` chip per cited chunk selects the passage. No custom component, no JavaScript.
- [ ] Source panel: metadata header, EDGAR link, surrounding text dimmed and the chunk highlighted, all escaped preformatted text. A test with filing text holding `<script>`, `|` and `*` shows it verbatim.
- [ ] Trace, in an expander, for each option: route; `graph_local` anchors and a `st.graphviz_chart` of anchor, edge and kept chunk (DOT labels escaped); Cypher attempts with outcome, row count and fallback, plus the retrieved chunk IDs (user decision 2026-11-04: no new field for the rows themselves); communities with title, summary, map points and reduce membership; `vector`'s ranked chunks. Display only.
- [ ] Metrics: a sample shows its committed per-question `dev` metrics, labelled "agent-drafted questions, judged uncalibrated"; every answer shows sentences kept and dropped, citations inside the retrieved chunks, and cost and latency per stage.
- [ ] Phoenix link shown only when tracing is on.
- [ ] Benchmarks tab: committed `benchmarks/results.md` rendered with HTML escaped, under a header repeating its labels.
- [ ] Errors from the backend (missing key, database down) shown in plain words.
- [ ] Playwright 1.64.0 in a dev-only dependency group; a screenshot script starts the app headless with dummy keys on a sample with a citation selected and the trace open, waits for the run to finish, and writes the PNG under `docs/img/`. Run by hand, not in CI; the PNG committed.
- [ ] Checked by hand in the browser: a sample answers through all six options under dummy keys (no spend).
- [ ] `uv run ruff check .` and the full `uv run --env-file .env pytest` pass; `mattpocock-skills:code-review` findings fixed; BUILD-LOG entry; branch, push and PR.
