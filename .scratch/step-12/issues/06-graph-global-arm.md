# 06: The `graph_global` arm, map and reduce

**What to build:** `eval.run --arm graph_global` answers a question end to end: map calls over the selected communities write scored map points citing chunks, a reduce call writes the answer with `answer/v1`'s status line, and the citation filter keeps only sentences citing chunks a map call was shown. Spec: `.scratch/step-12/spec.md` (`graph_global` arm steps 4–6 and config). Decision: ADR-0008. Vocabulary: CONTEXT.md (map point, arm).

**Blocked by:** 05

**Status:** ready-for-agent

**Parallel sessions:** one ticket at a time from here. `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket. Step 12 spend is capped at $6 in all, counting committed spend; show the estimate before any paid call.

- [ ] `prompts/global_map/v1.md` and `prompts/global_reduce/v1.md`, pinned by SHA-256, invented examples only. Map output `{points: [{text, score}]}`, score 0–100. Reduce output follows `answer/v1`'s status line and fixed sentences.
- [ ] Map: one call per selected community, 4 at a time; a point with no citation or citing a chunk outside its call's 6 is dropped and counted.
- [ ] Reduce: points above 0, by score then community rank, up to about 8k tokens. No point above 0 gives `not_found` with no reduce call.
- [ ] The existing citation filter runs with the allowed set being every chunk shown to any map call. `sources` are the shown chunks.
- [ ] Map and reduce on claude-sonnet-5-5 at effort `high` through the response cache; config pins N, chunks per call, the reduce budget, models, efforts and three prompt versions; the run header records them plus the communities and reports runs and ontology version.
- [ ] Stages and spans `community_select`, `global_map`, `global_reduce` with tokens and cost; prompt text only under `FILINGINTEL_TRACE_TEXT`. The per-question record holds selected communities, shown chunks, every map point (kept or dropped) and stage costs.
- [ ] Start-up refusals: `:GraphMeta` not at version 5 or a schema hash mismatch, uncached report texts, unpriced model. An API failure is an `ArmError`.
- [ ] Replies for 2–3 real dev global questions recorded by a capture script after its estimate. Tests through `Arm.run` on `neo4j-test` and Postgres: filter, dropped points, reduce ordering and cap, the `not_found` short-cut, a decline, the citation filter, stages and spans, the refusals.
- [ ] `mattpocock-skills:code-review`; ruff and pytest green; BUILD-LOG entry with spend.
