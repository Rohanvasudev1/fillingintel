# Step 14: Interface, then write-up

Status: ready-for-agent
Sources: RUNBOOK Step 14 and its stop condition (added 2026-10-20), the Step 13 outcome (BUILD-LOG 2026-10-19), docs/design/filingintel-demo.html (layout reference only), docs/ARCHITECTURE.md, docs/OPEN-DECISIONS.md, ADR-0001, ADR-0007, ADR-0008, CLAUDE.md Decisions (Step 14 entry), and the grilling of 2026-10-20. Every round was answered "as recommended", with one change from the user: three tickets instead of five (backend; UI; README and close). The test seams below were approved as proposed. External research: docs/research/streamlit-ui.md (Streamlit 1.66.0, citation rendering, caching, AppTest, graph drawing, Playwright, FastAPI trade-off, hosting). Vocabulary: CONTEXT.md (sample question, live question, answer trace, arm, router, routing map, chunk, filing, split, agent-drafted).

## Problem Statement

The benchmark is finished and committed, but nobody outside the repo can see the system work or read what it found.

- There is no interface. A reader cannot ask a question, see an answer with its citations, open the passage a citation points at, or see what a graph arm did. `ui/` and `api/` hold only empty `__init__.py` files, and no function answers one free-text question outside `eval.run`.
- The README is out of date and in the wrong order. It says the `test` run "comes in Step 13" (it ran on 2026-10-19), lists LangGraph and DeepEval in the stack (neither is used), and buries the results in per-step sections. The headline finding, that plain vector search beat every graph arm and both routers on `test` (pooled recall@10 0.923 against 0.439–0.711), is not stated anywhere a reader would look first.
- Numbers are copied by hand. The README's tables are typed from BUILD-LOG, so they can drift from the committed run files, which invariant 2 forbids.
- ARCHITECTURE.md describes things that were not built: a human-authored eval set, path precision, a Cypher floor in CI, a FastAPI layer.
- The repo has no licence, so by default nobody may reuse it.

## Solution

A local Streamlit app and a rewritten README.

The app has two tabs. **Ask** takes a question, lets the reader choose which arm (or router) answers it, and shows the answer with numbered citations. Clicking a citation chip shows the cited chunk highlighted inside its filing text, with a link to the filing on EDGAR. An answer trace shows what the arm did: the route, the anchors and edges, the Cypher and any repair, or the communities and map points. Sample questions from the `dev` split replay from the response cache for free and show their committed metrics. Typed questions run live, with the cost shown first and a per-session cap. **Benchmarks** shows the committed `results.md`.

The README follows the RUNBOOK's six parts: what it does with a screenshot, the `test` table up top, an honest interpretation, a short architecture section with one diagram, the numbers most projects don't publish, and what I'd do differently. Both number blocks are generated from the committed run files by `eval.results_table` and checked in CI, so they cannot drift.

## User Stories

Asking a question
1. As a reader, I want to type a question about NVDA, AMD or INTC filings and get an answer, so that I can see the system work on my own question.
2. As a reader, I want to choose which arm answers, so that I can compare `vector`, the graph arms and the routers myself.
3. As a reader, I want `vector` chosen by default with a note that it is the best measured arm, so that the default matches the benchmark.
4. As a reader, I want the router options labelled (dev-best "tuned on dev", pre-registered), so that I know which routing map I am using.
5. As a reader, I want sample questions I can click, so that I can try the app without thinking of a question.
6. As a reader, I want one sample per class (lookup, local, multi-hop, global, decline, unanswerable), so that I see each kind of behaviour, including declines and not-found answers.
7. As a reader, I want samples drawn by a fixed seed rather than hand-picked, so that the demo does not show only questions the system answers well.
8. As a reader, I want a list of every replayable `dev` question for the chosen arm, so that I can look past the six chips.
9. As the project owner, I want `test` questions never offered as samples, so that the held-out split stays out of the demo.
10. As a reader, I want sample answers to cost nothing and appear quickly, so that exploring is free.
11. As a reader, I want to see a typed question's cost estimate before it is sent, so that I know what it will spend.
12. As the project owner, I want a per-session spend cap from an environment variable, so that a long session cannot run up a bill.
13. As a reader, I want a clear message when the cap refuses a question, so that I know why nothing happened.
14. As a reader, I want a repeated question to replay for free, so that asking again does not spend again.
15. As a reader, I want a progress indicator while a live answer runs (3–16 s), so that I know the app is working.
16. As a reader, I want the last answer kept when I click around the page, so that a citation click does not lose it.
17. As a reader, I want an advice question declined with the fixed decline sentence, so that I see invariant 6 hold.
18. As a reader, I want a not-found answer shown with the fixed not-found sentence, so that I see the system say when the filings lack the evidence.
19. As a reader, I want a clear error message when a database is down or a key is missing, so that I know what to start or set.

Citations and sources
20. As a reader, I want each answer sentence to carry numbered citation markers, so that I can see which chunk backs which sentence.
21. As a reader, I want dropped sentences left out of the answer and counted, so that I see invariant 3 enforced.
22. As a reader, I want one chip per cited chunk that opens the source panel, so that I can check a claim against its passage.
23. As a reader, I want the source panel to show ticker, form, fiscal period, section and chunk ID, so that I know where the passage comes from.
24. As a reader, I want the cited chunk highlighted inside about 1,500 characters of its filing on each side, so that I can read it in context.
25. As a reader, I want the passage shown exactly as the filing text, not rendered as Markdown, so that tables and symbols are not changed.
26. As a reader, I want a link to the filing's EDGAR index page, so that I can open the original.
27. As the project owner, I want filing text escaped before it is shown, so that no filing content runs as HTML.

Answer trace
28. As a reader, I want to see the router's predicted class, its reason and the arm it chose, so that I can see why a question went where it did.
29. As a reader, I want `graph_local`'s anchors with label, similarity and how each matched, so that I can see what the question linked to.
30. As a reader, I want a small drawing of anchor, edge and chunk for `graph_local`, so that I can see which edges led to which chunks.
31. As a reader, I want each `graph_traversal` Cypher attempt with its outcome, the repair and any fallback, so that I can see what the model wrote and whether it ran.
32. As a reader, I want the rows a Cypher query returned, so that I can see what the graph gave back.
33. As a reader, I want `graph_global`'s selected communities with title and summary, so that I can see what themes it read.
34. As a reader, I want `graph_global`'s map points, their scores and which reached the reduce, so that I can follow the map-reduce.
35. As a reader, I want `vector`'s ranked chunks with scores, so that the control arm has a trace too.
36. As the project owner, I want the trace to be display only, never cited and never changing the answer, so that ADR-0007 holds.
37. As a developer, I want a link to the question's Phoenix trace when tracing is on, so that I can inspect spans.

Metrics
38. As a reader, I want a sample's committed per-question metrics (recall@5 and @10, wrong evidence, faithfulness, relevancy, citation support) from that arm's `dev` run, so that I can see how that answer scored.
39. As a reader, I want those metrics labelled "agent-drafted questions, judged uncalibrated", so that I do not over-read them.
40. As a reader, I want gold-free numbers for every answer (sentences kept and dropped, citations inside the retrieved chunks, cost and latency per stage), so that a typed question also gets measured.
41. As the project owner, I want no live judge calls in the UI, so that the app costs only the answer.

Benchmarks tab
42. As a reader, I want the committed `results.md` in the app, so that I can read the benchmark beside the demo.
43. As the project owner, I want the tab to render the committed file, not recompute it, so that it cannot differ from what CI checks.
44. As a reader, I want a header repeating the labels (agent-drafted, uncalibrated, `dev` is tuning data), so that the caveats are on screen.

Running it
45. As a developer, I want one command to start the app after `docker compose up -d`, so that running it is simple.
46. As the project owner, I want Streamlit's usage telemetry off in a committed config, so that the app sends nothing to Streamlit.
47. As the project owner, I want the app bound to 127.0.0.1, so that it is not exposed on the network.
48. As a developer, I want the UI to call the retrieval code in-process, with no second server, so that there is one process to run.

README
49. As a reader, I want three sentences and a screenshot saying what FilingIntel does, so that I understand it in seconds.
50. As a reader, I want the `test` benchmark table at the top, so that the result is not buried.
51. As a reader, I want the table to show recall@10 per class with n, pooled recall@10 and its paired difference against `vector` with intervals, faithfulness, cost and p95 latency, so that I see quality, cost and speed together.
52. As a reader, I want the labels above the table (`test`, 47 agent-drafted questions, judged uncalibrated, classes under 10 directional, commit), so that I know what the numbers rest on.
53. As a reader, I want an honest interpretation that says graph lost, by how much, and what it cost, so that I can trust the write-up.
54. As a reader, I want the `dev`-to-`test` drop (such as `graph_local` on local questions, 0.909 to 0.500) explained, so that I see what tuning on `dev` did.
55. As a reader, I want a short architecture section with a link to one diagram, so that I see how the parts fit.
56. As a reader, I want the numbers most projects don't publish (ER precision and recall, Cypher validity, router accuracy, extraction violation rate, persistence, provenance, corruption catch rates), each with its label and commit, so that I see the parts other projects hide.
57. As a reader, I want a "what I'd do differently" section in the author's voice, so that I see judgement, not just output.
58. As the project owner, I want both number blocks generated from committed files and checked in CI, so that the README never drifts from the harness (invariant 2).
59. As the project owner, I want to read and approve the interpretation and "what I'd do differently" in the PR, so that the prose is mine.
60. As a reader, I want "Running it" and "Scope" sections, so that I can reproduce it and know its limits.
61. As a reader, I want a licence, so that I know whether I may reuse the code.
62. As a reader, I want ARCHITECTURE.md and the README stack to describe what was built, so that the docs are accurate.

## Implementation Decisions

Ticket order: 01 backend, trace fields and samples; 02 the UI; 03 the README and closing the step. UI before README, so the screenshot and the README's first part show the UI that exists. Step 14 spend is capped at $2, with an estimate shown before any paid call; tests replay recorded replies.

**UI backend (new module, the test seam).** A small `Backend` the app is given at start, with three operations:
- answer a question with one picker option, returning a UI answer: the arm result, its total wall time, its cost breakdown (from the existing pure question-cost function), whether it replayed, and the trace id when tracing is on;
- fetch a source passage for a chunk ID: filing metadata (ticker from the CIK table, form, fiscal period, section, accession, CIK), the chunk's offsets, and the surrounding filing text;
- list the samples for an option, from the committed samples file, and the committed per-question metrics for a sample.
The real backend opens arms through the existing `ArmSpec.open` with the default response cache and runs `Arm.run(question)`; routers come from `ROUTER.with_map(...)`. Opened arms and the Postgres pool and Neo4j driver are cached for the app's life (Streamlit's resource cache); a Neo4j session per call. It checks each option's `required_env` and the model price table, and reports a missing key or an unreachable database as a typed error the app shows in plain words. It also keeps the session's spend and refuses a live question whose estimate would pass `FILINGINTEL_UI_MAX_SPEND` (default $1.00). The estimate is the option's mean committed `dev` cost per question (judges excluded). Nothing in the backend touches benchmark spend files or step caps.

**Picker options.** `vector` (default, "best measured arm"), `router (dev-best)` ("tuned on dev"), `router (pre-registered)`, `graph_local`, `graph_traversal`, `graph_global`.

**Trace fields on arm results.** `GraphRetrieval` gains the per-anchor edge support `graph_local` computes in ranking and now discards: for each kept chunk, which anchor and which edge (type, other end, evidence chunk) gave it support. `graph_global`'s map calls already carry community IDs; titles and summaries are read from `:Community` nodes by `community_id` when the backend builds the trace, not stored on the result. Traversal and router traces already exist (`cypher_attempts`, `fallback_reason`, `Routing`). Retrieval and ranking do not change. The new fields are not written by the run-file serializer, so every committed run file, its header and `frozen.json` stay byte-identical.

**Samples file.** A script runs every `dev` record's question through each picker option with dummy API keys, so a cache miss fails instead of spending, and writes the questions that replay per option, with their record IDs and classes, to a committed samples file. The selection logic is a pure function taking an arm opener and the records, so tests pass a fake opener. The six chips are drawn with a fixed seed, one per class, from the default option's replayable questions. Records in `test` are never read for samples. Any question not in the file for the chosen option is a live question.

**Ask view.** A question box in a form (typing does not rerun), the chips, the selectbox and the picker. While an answer runs, a status element shows progress; the result is kept in session state. The answer shows each kept sentence escaped with numbered markers; a row of pills, one per cited chunk, selects the passage shown in a side column. No custom component and no JavaScript. Decline and not-found answers show their fixed sentences. The source panel shows the metadata header, an EDGAR index link built from CIK and accession (no fetch), and the filing text from about 1,500 characters before `char_start` to 1,500 after `char_end`, dimmed, with the chunk in a highlight, all as escaped preformatted text. The trace is an expander: route; anchors table and a `st.graphviz_chart` DOT drawing for `graph_local` (DOT built with every label escaped); Cypher attempts with outcome, rows and fallback; communities with title, summary, map points and reduce membership; `vector`'s ranked chunks. Metrics: for a sample, the committed per-question record from that option's `dev` run file, labelled; for every answer, the gold-free numbers. With `PHOENIX_COLLECTOR_ENDPOINT` set, the backend opens an `answer_question` root span around each question and the app links to it; with it unset, no SDK object is built and no link shows.

**Benchmarks tab.** Renders committed `benchmarks/results.md` with `st.markdown` (HTML escaped by default) under a header repeating its labels. No free graph explorer.

**Dependencies and config.** `streamlit==1.66.0` as a main dependency, pinned exactly (approved). A committed `.streamlit/config.toml` sets `browser.gatherUsageStats = false`, `server.headless = true` and `server.address = "127.0.0.1"`. Playwright 1.64.0 in a dev-only dependency group (approved), used only by the screenshot script. `api/` is deleted; FastAPI is not used (Stack updated).

**Screenshot.** A script starts the app headless with dummy keys, opens a sample question with a citation selected and the trace open, waits until Streamlit reports the script is not running, and writes a PNG to `docs/img/`. Run by hand, never in CI.

**README generated blocks.** `eval.results_table` gains a second output: two blocks in the README between marker comments, rewritten in place, with the prose around them untouched. `--check` compares both `results.md` and the README blocks (exit 4 on a difference); CI already runs `--check`. Block 1, the `test` headline table: rows are the four arms and both routers; columns are recall@10 per answerable class with n, pooled recall@10 with its 95% interval, the pooled paired difference against `vector` with its interval, pooled faithfulness (uncalibrated), mean cost per question, and p95 total latency; a line above it gives split, question count, labels and commit; one line below gives decline and unanswerable results. Block 2, "numbers most projects don't publish": one row per figure with its value, interval where one exists, label, commit and a link to its `results.md` section: ER precision and recall (round 2), Cypher validity first pass and after repair, router `test` accuracy with Wilson interval, extraction violation rate and rejection rate with both review rounds, persistence precision and recall, `graph_global` provenance breaks, and corruption-test catch rates. All values come from the same input files `results.md` reads.

**README prose.** Parts in order: what it does (three sentences, screenshot); block 1; interpretation; architecture (about 8 lines, link to the diagram); block 2; what I'd do differently; Running it (compose, env, load, app command); Scope. The per-step sections are removed; their figures are in `results.md`. The interpretation and "what I'd do differently" are drafted by the agent from BUILD-LOG's `test` entry and "Findings worth telling", with every number traced to a committed file, and the user edits them in review. Judged numbers say "uncalibrated"; calibration stays in OPEN-DECISIONS.

**Docs and licence.** ARCHITECTURE.md's ASCII diagram is replaced by one Mermaid flowchart of what was built (ingest, Postgres, extraction, resolution, Neo4j, communities, the four arms, the router, the answer filter, the eval harness, `results.md`, Streamlit), and its drift fixed (eval set is agent-drafted; no path precision; no Cypher floor in CI; no FastAPI). The README stack drops LangGraph, DeepEval and FastAPI. The RUNBOOK repo tree drops `api/`. An MIT LICENSE, with a README line that the filings are SEC public records and the questions are agent-drafted.

## Testing Decisions

A good test checks behaviour a reader or caller can see: what the page shows after an action, what a function returns, what a file contains. It does not check widget internals, private helpers or call order.

- **The app, through the backend seam.** AppTest drives the whole page in-process against a fake backend, offline: default option, choosing a sample, asking a live question, the cost estimate and the cap refusing, a missing-key or database error, a decline and a not-found answer, a citation pill opening the right passage, the trace for each option, the metrics for a sample and for a live answer, the Phoenix link present only when tracing is on, and the Benchmarks tab showing the committed file. Escaping is tested with filing text holding `<script>`, `|` and `*`.
- **The real backend.** Tested against replies already recorded in the test fixtures and the test databases, as the arm tests do: answer, source passage (offsets and surrounding text against `resolve()`), samples and metrics lookup, spend cap arithmetic.
- **Trace fields.** Through the existing arm tests (`graph_local`, `graph_traversal`, `graph_global`, router): the new edge support matches what ranking used. A run record serialised from a result with the new fields is byte-identical to the committed form, and committed run files and `frozen.json` are unchanged.
- **Samples builder.** The pure selection function with a fake opener that raises on a cache miss: only replaying questions listed, no `test` record read, the seeded chips stable and one per class.
- **Config.** A test reads `.streamlit/config.toml` and checks telemetry off, headless and the 127.0.0.1 address.
- **README blocks.** As `results.md` is tested now: built from fixture inputs, stable under two hash seeds, the prose outside the markers untouched, `--check` exit 4 when a block differs.
- **Not tested:** the screenshot script and the README prose, which are run or read by hand.

Prior art: the arm tests and their data modules (`graph_local`, traversal, global, router fakes), the results-table tests, the eval.run router tests, and the tracing tests with an in-memory exporter.

## Out of Scope

- Hosting, a public URL, or any deployment.
- A FastAPI or other HTTP layer.
- A free graph explorer; the graph is drawn only inside an answer's trace.
- Custom Streamlit components or any JavaScript build.
- Live judge calls in the UI.
- Path context in answers, path citations and path precision (OPEN-DECISIONS).
- Any change to retrieval, prompts, routing maps or `test` results.
- Judge calibration (deferred; OPEN-DECISIONS).
- Step 13b agentic arms.
- `test` questions as samples.
- A GIF or video.

## Further Notes

- Some `dev` requests changed after they were cached (`cypher/v1`'s rendered hash moved with ontology 5), so a Step 11 traversal sample may not replay. The samples file decides this from fact, not assumption.
- AppTest cannot click custom components, which is one reason the citations use native pills.
- A rerun from a widget click does not cancel a running answer call; the result is kept in session state, and the response cache makes a repeat free.
- Live questions write to the same response cache as the benchmark. That is harmless (keys are whole requests), but UI spend is never counted as step spend.
- The dev Neo4j and Postgres must be running for any answer, samples included, since replay still retrieves.
