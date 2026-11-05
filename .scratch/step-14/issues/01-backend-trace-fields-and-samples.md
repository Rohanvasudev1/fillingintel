# 01: Backend, trace fields and samples

**What to build:** one function call answers any question through any of the six picker options and returns everything the UI will show: the answer with its kept and dropped sentences, the answer trace, cost and latency per stage, whether it replayed, and the trace id when tracing is on. The same backend fetches a cited chunk's source passage in its filing context, lists the sample questions that replay for each option, and finds a sample's committed `dev` metrics. `graph_local` results now say which anchor and edge supported each kept chunk, and nothing written to run files changes. It ends with the committed samples file, built under a cache-only check. Spec: `.scratch/step-14/spec.md` (UI backend; Picker options; Trace fields; Samples file). Vocabulary: CONTEXT.md (sample question, live question, answer trace, arm, router, routing map).

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Parallel sessions:** one ticket at a time. `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session runs pytest, with dev Neo4j stopped. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. Step 14 spend is capped at $2; show an estimate before any paid call (this ticket should need none).

- [ ] A `Backend` interface with three operations: answer (question, picker option) → UI answer; source passage (chunk ID) → metadata, offsets and surrounding text; samples (option) → replayable sample questions, with a lookup of a sample's committed per-question metrics. A real implementation and a fake for tests.
- [ ] The six picker options: `vector` (default), `router (dev-best)`, `router (pre-registered)`, `graph_local`, `graph_traversal`, `graph_global`, each opened through the existing arm specs (routers through `with_map`), opened once and reused.
- [ ] The UI answer carries the arm result, total wall time, the cost breakdown from the existing pure question-cost function, a replayed flag, and the trace id when `PHOENIX_COLLECTOR_ENDPOINT` is set (an `answer_question` root span per question). With tracing off, no SDK object is built.
- [ ] A missing key, an unpriced model or an unreachable Postgres or Neo4j comes back as a typed error with a plain message, not a traceback.
- [ ] Source passage: ticker (from the CIK table), form, fiscal period, section, accession, CIK, `char_start`/`char_end`, and the filing text from about 1,500 characters before to 1,500 after, matching `resolve()` on the chunk itself. An EDGAR index URL built from CIK and accession, with no fetch.
- [ ] Spend cap: an estimate per option from its mean committed `dev` cost per question (judges excluded); a live question whose estimate plus the session's spend passes `FILINGINTEL_UI_MAX_SPEND` (default $1.00) is refused before any call. A replayed answer adds nothing. Nothing touches benchmark spend files or step caps.
- [ ] `GraphRetrieval` gains `graph_local`'s per-chunk edge support (anchor, edge type, other end, evidence chunk), the same support ranking used; ranking and retrieved chunks are unchanged, shown by the existing `graph_local` and traversal-fallback tests.
- [ ] For `graph_global`, the backend reads each selected community's title and summary from `:Community` by `community_id`.
- [ ] The run-file serializer does not write the new fields: a test serialises a result with them and gets the committed form, and the committed run files and `frozen.json` are unchanged.
- [ ] A pure samples-selection function (arm opener, records → replayable questions per option) and a script that runs it with dummy API keys over every `dev` record, writing the committed samples file with record ID, class and question per option, plus the six seeded chips (one per class) from `vector`'s list. `test` records are never read. Tests use a fake opener that raises on a cache miss.
- [ ] The samples file is built against the dev databases, and BUILD-LOG says how many questions replay per option and why any did not.
- [ ] `uv run ruff check .` and the full `uv run --env-file .env pytest` pass; `mattpocock-skills:code-review` findings fixed; BUILD-LOG entry; branch, push and PR.
