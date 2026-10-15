# 07: Full dev run, provenance check and closing Step 12

**What to build:** `graph_global` runs the 10 global, 6 decline and 5 unanswerable dev questions end to end with answers and judges, is compared with `vector`, and every kept citation is traced through community, member and evidence to its chunk. Step 12 closes with its stop condition read and recorded. Spec: `.scratch/step-12/spec.md` (Runs and spend; Provenance check; Stop condition reading; Docs). RESEARCH-PLAN H4 and H5.

**Blocked by:** 01, 06

**Status:** ready-for-agent

**Parallel sessions:** one ticket at a time from here. `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket. Step 12 spend is capped at $6 in all, counting committed spend; show the estimate before any paid call.

- [ ] `python -m graph.global_provenance <run file>`: for every kept sentence's citations, finds a selected community with a member whose `EVIDENCED_BY` reaches the chunk, or an extracted edge among its members citing it. Exit 0 no breaks, 2 bad arguments, 3 could not run, 4 breaks. Writes `benchmarks/graph/global-provenance-run{N}.json` with every trace. Tested on a run file with one deliberately broken citation.
- [ ] Estimate shown before the run; refuses above the Step 12 cap's remainder.
- [ ] `env -u ANTHROPIC_BASE_URL uv run --env-file .env python -m eval.run --arm graph_global --classes global,decline,unanswerable` on `dev`; the run file committed under `benchmarks/runs/`.
- [ ] `eval.compare` against the vector baseline on the shared questions: recall@5, recall@10, wrong evidence, judged relevancy, faithfulness and citation support, labelled agent-drafted, tuned on dev, directional, uncalibrated where judged.
- [ ] Stop condition: global recall@10 interval excludes 0, and the provenance check exits 0. If either fails, stop and report to the user before closing.
- [ ] H4 reported with its interval; H5 cost per query reported (latency only for live stages).
- [ ] RUNBOOK Step 12 outcome, README section (communities, reports, the global result, cost), CLAUDE.md Decisions and Commands, OPEN-DECISIONS updated; BUILD-LOG entry with total Step 12 spend and a "Next session starts with" note (Step 13 grilling).
- [ ] `mattpocock-skills:code-review`; ruff and pytest green.
