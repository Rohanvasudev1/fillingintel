# 05: Choosing communities and N, retrieval only

**What to build:** Given a question, the graph_global retrieval ranks lower-level community reports, picks the top N with evidence in the question filter, and chooses each community's 6 closest filtered chunks. `python -m eval.global_n` scores N ∈ {4, 6, 8, 12} at no model cost and freezes N. Spec: `.scratch/step-12/spec.md` (Report embeddings; `graph_global` arm steps 1–3; N tuning). Decision: ADR-0008.

**Blocked by:** 04

**Status:** ready-for-agent

**Parallel sessions:** one ticket at a time from here. `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket. Step 12 spend is capped at $6 in all, counting committed spend; show the estimate before any paid call.

- [ ] `retrieve.node_embed` also embeds report texts ("title: summary") through the cached path; estimate first, `--embed` to send (a few cents).
- [ ] Selection: exact cosine between the question's cached query embedding and every lower report with a report; keep, in rank order, the top N communities with evidence chunks in the filtered filings (ADR-0001); ties by `community_id`. Retrieval refuses uncached report texts.
- [ ] Per community: its evidence chunks inside the filter, ranked by cosine against `chunk_embeddings`, top 6.
- [ ] The ranked list is the shown chunks by community rank then cosine, deduplicated, scored at 5 and 10 like other arms.
- [ ] `python -m eval.global_n` (offline, $0) scores N ∈ {4, 6, 8, 12} on the 82 answerable dev questions, picks N by the 10 global questions' recall@10 (ties to the smaller), adds a retrieval-only ablation over top-level reports, and writes `benchmarks/graph/global-n-run1.json` (committed), labelled "tuned on dev, n = 10" and agent-drafted.
- [ ] N is frozen in the arm config.
- [ ] Test-first on `neo4j-test` and Postgres with cached embeddings: the filter drops other companies' and periods' communities and chunks, ranking and ties, uncached report refusal, the ranked list's order.
- [ ] `mattpocock-skills:code-review`; ruff and pytest green; BUILD-LOG entry with the N table.
