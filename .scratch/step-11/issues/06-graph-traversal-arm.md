# 06: graph_traversal arm

**What to build:** `eval.run --arm graph_traversal` runs every dev record. Claude writes Cypher from the generated schema, the guard checks and runs it, and the returned `chunk_ids` become at most 10 ranked chunks. A failure gets one repair, then falls back to `graph_local`. Every attempt and fallback is recorded, and a Cypher validity report is written. Spec: `.scratch/step-11/spec.md` (`graph_traversal`). Decisions: ADR-0007. Research: docs/research/text-to-cypher-and-local-search.md (section B). Vocabulary: CONTEXT.md (fallback, Cypher validity rate).

**Blocked by:** 04 (guard), 05 (graph_local, the fallback)

**Status:** done (PR pending review)

**Parallel sessions:** other Step 11 tickets may run at the same time in their own worktrees. `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest, and iterate with `NEO4J_TEST_URI` unset when your tests do not need Neo4j. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket.

- [x] `prompts/cypher/v1.md`: system part with the generated `schema_text()`, the 24 filings (ticker, form, fiscal period, accession number) and the rules (one read query, return a `chunk_ids` list from `EVIDENCED_BY` chunks and edge `chunk_ids`, only listed names, no LIMIT needed); 4–5 invented worked examples, never eval questions and never the Step 10 multi-hop queries. User template: the question plus the parsed filter. Pinned by SHA-256; version `cypher/v1@<hash8>`.
- [x] claude-sonnet-5-5 at effort `high`, structured output `{reasoning, cypher}`, through the response cache.
- [x] Ticket 02's seams: `retrieve/arms.py` registers the arm with `required_env` (Neo4j settings, `DATABASE_URL`, `VOYAGE_API_KEY`, `ANTHROPIC_API_KEY`); the Cypher model is in `ArmConfig.models` and `ArmConfig.graph` holds the Cypher prompt version and hash; generation and repair stages are priced with `StageCost.anthropic` (the header's price table); prompt text goes on `cypher_reply_attributes` only when `SpanRecorder.capture_text` is on.
- [x] The arm refuses to start unless `:GraphMeta` matches the code's ontology version and schema hash.
- [x] One repair, with the failed query and the guard message, on a guard failure, error, timeout or no chunk IDs. Then fallback to `graph_local`, recorded as `invalid`, `error`, `timeout` or `empty`. Both attempts are kept.
- [x] Rows are flattened in order, deduplicated, filtered and cut to 10.
- [x] `benchmarks/graph/cypher-validity-run{N}.json`: commit, prompt version, model and effort, per class the four rates (first-pass valid, valid after repair, non-empty, fallback), and per question every attempt and the fallback reason.
- [x] A script captures real replies for a few fixture questions. Tests through `Arm.run` on `neo4j-test`: happy path, a guard failure repaired, an empty result repaired then fallen back, every fallback reason recorded, a `:GraphMeta` mismatch refusing to start.
- [x] A retrieval-only dev run with the estimate shown first. At most one `cypher/v2`, written from failure categories. Both validity reports are committed and BUILD-LOG says which version is used.
- [x] `mattpocock-skills:code-review`; ruff and pytest green.
