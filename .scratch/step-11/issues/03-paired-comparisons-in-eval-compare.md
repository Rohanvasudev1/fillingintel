# 03: Paired comparisons in eval.compare

**What to build:** `eval.compare` reports, per class, the paired difference between two runs on the questions both hold, with a fixed-seed bootstrap 95% interval, and marks the differences whose interval excludes 0, as RESEARCH-PLAN requires. Verifiable now on the baseline vector run against its uncached repeat. Spec: `.scratch/step-11/spec.md` (Harness seams, `eval.compare`). Closes the OPEN-DECISIONS item "paired comparisons between arms".

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Parallel sessions:** other Step 11 tickets may run at the same time in their own worktrees. `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest, and iterate with `NEO4J_TEST_URI` unset when your tests do not need Neo4j. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket.

- [ ] For each class and pooled: paired differences in recall@5, recall@10, precision@5, precision@10 and wrong-evidence rate, and in each judged mean, labelled "uncalibrated".
- [ ] Fixed-seed paired bootstrap over shared question IDs. The output shows the mean difference, the 95% interval, n, and whether the interval excludes 0. Classes under 10 shared questions are marked "directional".
- [ ] Questions present in only one run are counted and listed, not silently dropped.
- [ ] Runs from different answer prompts are still refused. Comparing two different arms is allowed.
- [ ] Test-first on small run files: reproducible intervals for a fixed seed, a zero-difference case not counted, a clear-difference case counted.
- [ ] Shown on the committed baseline against the local uncached repeat (`-2.json`, if present); the output goes into the BUILD-LOG entry.
- [ ] `mattpocock-skills:code-review`; ruff and pytest green; OPEN-DECISIONS untouched (already closed in the plan).
