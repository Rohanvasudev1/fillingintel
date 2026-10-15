# 01: Judging race fix and `eval.run --classes`

**What to build:** A paid run can no longer fail at judging on the OpenAI `Response` race, and a full run can be limited to chosen classes, so Step 12's full run covers only the 10 global, 6 decline and 5 unanswerable dev questions. Both are harness changes the user approved on 2026-10-15. Spec: `.scratch/step-12/spec.md` (Ticket 01; Runs and spend). Background: BUILD-LOG Step 11 ticket 07 (the judging failure on the first `graph_local` attempt).

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Parallel sessions:** tickets 01 and 02 may run at the same time in their own worktrees. `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest, and iterate with `NEO4J_TEST_URI` unset when your tests do not need Neo4j. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket. Step 12 spend is capped at $6 in all; show the estimate before any paid call.

- [ ] The OpenAI `Response` model is rebuilt once when the OpenAI judge backend is imported, so judge threads never build it concurrently.
- [ ] A test parses a recorded luna reply from several threads at once with the network blocked. Show the race fails or is plausible first if it can be reproduced; if not, say so in the BUILD-LOG and keep the test as a guard.
- [ ] Replaying the committed baseline's judge replies gives identical scores (no score changes).
- [ ] `eval.run --classes <class,...>` limits the run to those classes; an unknown class or an empty list is a bad-input error. Without the option, behaviour is unchanged.
- [ ] The run header records the class selection, and `eval.compare` pairs a class-limited run against a full run on the shared questions (unpaired counts as before).
- [ ] Test-first: the selection, the bad inputs, the header field, and a compare against a full run.
- [ ] The judging-race item is removed from docs/OPEN-DECISIONS.md; CLAUDE.md Commands mention `--classes`.
- [ ] `mattpocock-skills:code-review`; ruff and pytest green; BUILD-LOG entry.
