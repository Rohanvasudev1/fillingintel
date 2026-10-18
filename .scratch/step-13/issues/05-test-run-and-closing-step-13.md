# 05: The `test` run and closing Step 13

**What to build:** The held-out `test` split is scored once with `--final` for every row, and `benchmarks/results.md` leads with the `test` table. BUILD-LOG explains every cell, including where graph and the router lose, which meets the Step 13 stop condition. Spec: `.scratch/step-13/spec.md` (Freeze, then `test` once; Results module; Spend). Plan: docs/RESEARCH-PLAN.md (H1–H6, amendment of 2026-10-16).

**Blocked by:** 03, 04

**Status:** ready-for-agent

**Parallel sessions:** one ticket at a time (user, 2026-10-16). `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest, with dev Neo4j stopped. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket. Step 13 spend is capped at $10 in all, counting committed spend; show the estimate before any paid call. Show the full `test` estimate to the user and get a yes before the first `--final` run.

- [ ] Before any `test` call (user decision, 2026-10-18): `eval.run` refuses a run whose estimate plus committed Step 13 spend (`eval/step13_spend.py`) passes $10, and its estimate prices questions whose answers are already in the response cache at their uncached remainder (classification, judges or nothing), not as fresh calls, so the check does not refuse runs that fit. It changes no arm config, so `frozen.json` stays valid.
- [ ] `frozen.json` matches the live config before any `test` call; nothing in it changes during this ticket.
- [ ] `--final` runs on the 47 `test` questions: `vector`, `graph_local`, `graph_traversal` in full; `graph_global` in full on global, decline and unanswerable (14) and retrieval-only on lookup, local and multi_hop (33); the router under both maps. About $6.75; all run files committed.
- [ ] A run that stops partway is resumed with the same commit and config. A bug found after scoring is fixed and rerun only under the spec's rule, both runs committed and footnoted.
- [ ] `results.md` regenerated with the `test` table first, paired differences on `test`, H1–H6 verdicts, the router section with `test` accuracy and confusion matrix, and the dev tables last. `--check` passes in CI.
- [ ] Step 13 spend summed from committed run files and recorded replies, under $10.
- [ ] BUILD-LOG entry explaining every cell of the `test` table and each H verdict, with commit and labels; RUNBOOK Step 13 outcome; OPEN-DECISIONS and CLAUDE.md updated; a "Findings worth telling" line.
- [ ] `mattpocock-skills:code-review`; ruff and pytest green.
