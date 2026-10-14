# 02: Arm seams in the harness

**What to build:** The eval harness can carry a graph arm's retrieval details, costs and provenance without any graph arm existing yet. A stub arm in tests produces a run file with the new fields; `vector` runs and `eval.gate` are unchanged. This prefactor keeps tickets 05 and 06 from both editing the harness. Spec: `.scratch/step-11/spec.md` (Harness seams). Decisions: ADR-0007; CLAUDE.md Decisions, Step 11 (harness approved).

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Parallel sessions:** other Step 11 tickets may run at the same time in their own worktrees. `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest, and iterate with `NEO4J_TEST_URI` unset when your tests do not need Neo4j. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket.

- [x] `Retrieval` gains optional graph fields: anchors (key, label, similarity, how matched), hop count, a no-anchor flag, and Cypher attempts with their outcomes, the fallback reason and validity flags. Vector results leave them empty.
- [x] Per-question operational records gain stages for anchor linking, Cypher generation, repair and execution (latency, tokens, cost), priced from the existing price tables. A model with no price still refuses to start.
- [x] The run header records the Cypher prompt version and hash, ontology version, schema hash and graph load-run ID when the arm provides them.
- [x] Per-question records carry each retrieved chunk's best evidence confidence when the arm provides it.
- [x] Span names for anchor linking, graph expansion, Cypher generation, the guard, execution and fallback are defined. A Cypher text attribute is always set; prompt text only under `FILINGINTEL_TRACE_TEXT`.
- [x] Run files record no-anchor and not-found counts per class.
- [x] Tests (prior art: the eval.run and tracing-run tests) drive a stub arm end to end and check the new fields. The existing vector, gate and compare tests pass unchanged.
- [x] `mattpocock-skills:code-review`; ruff and pytest green; BUILD-LOG entry.
