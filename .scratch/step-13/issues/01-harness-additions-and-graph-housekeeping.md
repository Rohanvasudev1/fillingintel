# 01: Harness additions and graph housekeeping

**What to build:** `eval.run` records how long each question took end to end, refuses a run whose estimate exceeds `--max-cost`, and can score retrieval alone with `--retrieval-only`. The graph says which load run it came from, and the query-embedding cache can no longer change a vector already written. It ends with `graph_global` scored retrieval-only on the 82 answerable dev questions, at no cost, so later tickets have per-question recall for it. Spec: `.scratch/step-13/spec.md` (Harness; Graph and cache items; Dev runs step 1). Vocabulary: CONTEXT.md (arm, router).

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Parallel sessions:** one ticket at a time (user, 2026-10-16). `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest, with dev Neo4j stopped. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket. Step 13 spend is capped at $10 in all, counting committed spend; show the estimate before any paid call.

- [ ] Every question record holds `total_ms`, wall time around the whole `arm.run` call. Cells give p50 and p95 of `total_ms` with bootstrap intervals and count questions with a replayed call; those questions are flagged. Existing per-stage latency is unchanged.
- [ ] Each arm spec supplies an estimator over the selected records (retrieval, answer and judge calls), reusing the Cypher and global estimators. `eval.run` prints the estimate before opening the arm, and with `--max-cost` refuses above it with exit 2, opening nothing.
- [ ] `--retrieval-only` makes no answer or judge call and writes a normal results file whose header records the mode, with retrieval metrics and retrieval latency per cell and no answer, judged or cost fields. It works with `--classes` and with `--final` on `test`.
- [ ] `graph.load --rebuild` writes the load run name onto `:GraphMeta` in the rebuild transaction; `validate_graph()` reports its absence; the graph arms read it from `:GraphMeta` for the run header and refuse to open without it. Ontology version and schema hash are unchanged. The dev graph is rebuilt once to carry it.
- [ ] A second write of the same key to the query-embedding cache keeps the existing vector.
- [ ] Tests, each seen failing first: through `eval.run` (`total_ms`, flags, the refusal, retrieval-only files with no model call), on `neo4j-test` (load run written, validated, read, refusal), the cache (second write kept out).
- [ ] `eval.run --arm graph_global --retrieval-only` on the 82 answerable dev questions, from cached embeddings ($0), committed under `benchmarks/runs/` (force-added).
- [ ] `mattpocock-skills:code-review`; ruff and pytest green; BUILD-LOG entry.
