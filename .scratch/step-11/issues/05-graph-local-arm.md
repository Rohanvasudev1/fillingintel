# 05: graph_local arm

**What to build:** `eval.run --arm graph_local` runs every dev record. Each question is linked to anchors, their evidence and their edges' evidence are collected, and at most 10 ranked chunks go to the unchanged answer prompt. Retrieval-only dev scores pick 1 or 2 hops, and the choice is frozen. Spec: `.scratch/step-11/spec.md` (`graph_local`). Decisions: ADR-0007, ADR-0001, ADR-0002. Vocabulary: CONTEXT.md (anchor, arm, question filter).

**Blocked by:** 01 (hop choice scored on the v4 graph), 02 (harness seams)

**Status:** ready-for-agent

**Parallel sessions:** other Step 11 tickets may run at the same time in their own worktrees. `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest, and iterate with `NEO4J_TEST_URI` unset when your tests do not need Neo4j. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket.

- [ ] Node embeddings: "Label: name" for cross-filing labels, "RiskFactor: title", "MetricValue: concept, period". Voyage `document` input through the existing entity-embedding cache. The cost estimate is shown before the first embedding run.
- [ ] Anchors: RapidFuzz name or alias matches against phrases of the question come first (the cutoff is chosen here and recorded), then exact cosine against the cached query embedding, up to 20. Filers are excluded. An anchor needs evidence in the filtered filings.
- [ ] Expansion in a READ transaction, using the read-session helper from ticket 04 if it has merged, otherwise the existing one. 1 hop: the anchor's `EVIDENCED_BY` chunks plus the `chunk_ids` of every edge touching it, `PERSISTS_AS` included. 2 hops: also the neighbours' `EVIDENCED_BY` chunks.
- [ ] Score: the sum over anchors of similarity × (1 + the anchor's edges citing the chunk); name matches count as similarity 1.0. The filter is applied, ties break by chunk ID, and the list is cut to 10. No anchor or no chunk gives an empty retrieval.
- [ ] The arm fills ticket 02's fields (anchors, hops, no-anchor flag, stage costs, best confidence) and its spans.
- [ ] Test-first through `Arm.run` on `neo4j-test`, with a hand-built graph around real fixture chunk IDs and fake embedders: ranking and ties, a name match beating a closer embedding, filers never anchored, the filter dropping another period, no anchor giving empty.
- [ ] Retrieval-only dev runs at 1 and 2 hops. The higher pooled recall@10 is frozen in config (a tie goes to 1 hop), and both scores go to BUILD-LOG as dev-tuned.
- [ ] `mattpocock-skills:code-review`; ruff and pytest green.
