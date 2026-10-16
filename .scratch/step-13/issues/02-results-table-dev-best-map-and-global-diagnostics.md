# 02: `results.md` from committed runs, the dev-best map and two global diagnostics

**What to build:** `python -m eval.results_table` builds `benchmarks/results.md` offline from the committed run files, and CI fails when the committed file differs. The first version holds the dev tables (labelled as tuning data), paired differences against `vector`, the H1–H5 verdicts and the other measured numbers. The same module chooses the dev-best routing map by the fixed rule and writes it to a file. `graph.global_provenance` gains the strict count, and the `graph_global` chunk-handoff ablation is scored on dev retrieval only. Spec: `.scratch/step-13/spec.md` (Results module; CI; Graph and cache items, last two bullets; Dev runs step 3). Vocabulary: CONTEXT.md (routing map, arm, control arm).

**Blocked by:** 01

**Status:** ready-for-agent

**Parallel sessions:** one ticket at a time (user, 2026-10-16). `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest, with dev Neo4j stopped. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket. Step 13 spend is capped at $10 in all, counting committed spend; show the estimate before any paid call.

- [ ] `eval.results_table` reads only committed files, with no network or database call, and its output is byte-identical across runs. A header lists every input file with SHA-256 and commit, and states that every number is on agent-drafted questions.
- [ ] Table layout as the spec lists, built so the `test` table goes on top once `test` run files exist (ticket 05). Rows by arm and class, decline and unanswerable rows; columns n, recall@5/@10, precision@10, wrong evidence, faithfulness, relevancy, citation support, p50/p95 `total_ms`, cost, each with its interval. Retrieval-only cells print "retrieval only". Judged columns carry their label (uncalibrated until ticket 04).
- [ ] Paired differences against `vector` reuse `eval.compare`'s computation; "directional" where n < 10.
- [ ] H1–H5 verdicts, each printing its RESEARCH-PLAN rule and the numbers it read (H6 comes in ticket 03).
- [ ] The "numbers most projects don't publish" block: Cypher validity, ER precision and recall (rounds 1 and 2), extraction accuracy and violation rate, persistence precision and recall, provenance counts, the handoff ablation; each with commit, file and label.
- [ ] The dev-best rule: per answerable class the arm with the highest dev recall@10; arms whose paired difference against it includes 0 tie; ties go to the lowest dev cost per question. Written once to `benchmarks/router/dev-best-map.json` with the intervals that decided it.
- [ ] `--check` exits 4 on any difference and 0 on a match; the lint-and-test CI job runs it.
- [ ] `graph.global_provenance` writes a strict count beside the existing one: a citation passes only through the community of the kept map point that cited it. The existing count stays the gate. Rerun on the Step 12 run and committed.
- [ ] `python -m eval.global_handoff` (offline, cached embeddings) scores the top-10 chunk handoff against the arm as built on the 82 answerable dev questions; `benchmarks/graph/global-handoff-run1.json` labelled "ablation, dev". The arm is unchanged.
- [ ] Tests, seen failing first: on the real committed dev run files (rows, columns, retrieval-only cells, paired differences equal to `eval.compare`'s, each H verdict on a case near its threshold, the dev-best rule including a tie, `--check`); the strict provenance count on a run with one citation from the wrong community; the handoff ranking.
- [ ] First `results.md` committed (dev only). `mattpocock-skills:code-review`; ruff and pytest green; BUILD-LOG entry.
