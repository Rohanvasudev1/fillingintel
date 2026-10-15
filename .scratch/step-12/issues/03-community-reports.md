# 03: Community reports

**What to build:** `python -m graph.reports` writes a checked, cited report for every reportable community, lower level first and then top, and commits them to a reports run file that the graph load can match by member hash. Spec: `.scratch/step-12/spec.md` (Reports; `python -m graph.reports`). Research: docs/research/communities-and-global-search.md (section 2.2). Vocabulary: CONTEXT.md (community report).

**Blocked by:** 02

**Status:** ready-for-agent

**Parallel sessions:** one ticket at a time from here. `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket. Step 12 spend is capped at $6 in all, counting committed spend; show the estimate before any paid call.

- [ ] `prompts/report/v1.md`, pinned by SHA-256; version `report/v1@<hash8>` covers the system prompt, user template and output schema. Structured output `{title, summary, findings: [{text}]}`; the summary and findings cite `[chunk_id]`. Invented examples only, never corpus text.
- [ ] Lower report input: members with labels and names, edges among members with their evidence spans, and the most-cited member chunk texts up to about 6k input tokens. Top report input: its lower reports' kept text.
- [ ] Citation check: a sentence is kept only if it cites at least one chunk and every cited chunk is in the community's evidence (lower: members' evidence; top: chunks kept in its lower reports). Dropped sentences are counted per report. `report_chunk_ids` is the set the kept sentences cite.
- [ ] claude-haiku-5-5 at effort `high`, structured output, through the existing client and response cache, price table 2026-10-10. 4 calls at a time.
- [ ] The CLI checks `ANTHROPIC_API_KEY`, refuses while `ANTHROPIC_BASE_URL` is set, prints an estimate and sends nothing without `--send`, refuses above `--max-cost` (default $1) or above the Step 12 cap counting committed spend.
- [ ] Replies for 2–3 real communities recorded by a capture script after its estimate; tests replay them offline: request shape and pinned hash, the citation check (uncited and out-of-community sentences dropped), top reports limited to kept lower citations, estimate and cap refusals.
- [ ] Run 1 writes `benchmarks/communities/reports-run{N}.jsonl` (committed): per community `community_id`, `member_hash`, level, title, kept report text, `report_chunk_ids`, dropped count, prompt version, tokens, cost; plus a summary header with totals.
- [ ] `mattpocock-skills:code-review`; ruff and pytest green; BUILD-LOG entry with spend.
