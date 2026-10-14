# 07: Full dev runs and closing Step 11

**What to build:** Both graph arms run every dev record end to end, with answers and 3 judge passes. Each is compared with the baseline `vector` run, the results are committed, and Step 11 is closed against its stop condition. Spec: `.scratch/step-11/spec.md` (Runs and closure). RUNBOOK Step 11.

**Blocked by:** 03 (paired comparisons), 06 (graph_traversal)

**Status:** ready-for-agent

**Parallel sessions:** other Step 11 tickets may run at the same time in their own worktrees. `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest, and iterate with `NEO4J_TEST_URI` unset when your tests do not need Neo4j. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket.

- [ ] The cost estimate for both runs is shown first. Step 11's total spend (embeddings, Cypher calls, runs) stays within $15. The user says go before paid runs.
- [ ] One full dev run each for `graph_local` and `graph_traversal`. Both run files and the final Cypher validity report are committed.
- [ ] `eval.compare` of each arm against the baseline vector run, with paired intervals per class, goes into BUILD-LOG with commits.
- [ ] Labels everywhere: agent-drafted questions, judged numbers uncalibrated, graph dev numbers tuned on (hop choice, prompt revision).
- [ ] RUNBOOK Step 11 gets its outcome: the stop condition (both arms run the full dev set end to end; Cypher validity rate reported) is met or not, said plainly, including where graph loses.
- [ ] README rows for the two arms and the Cypher validity rate.
- [ ] CLAUDE.md Decisions gets any choices made in tickets 01–06 that are not yet recorded. OPEN-DECISIONS is updated.
- [ ] `mattpocock-skills:code-review`; ruff and pytest green; BUILD-LOG entry with "Next session starts with".
