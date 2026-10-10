# 02: Resolution without an LLM: deterministic merges and candidate pairs

**What to build:** the first end-to-end path of the `resolve` package. `python -m resolve.run` reads extraction outputs, applies authoritative keys and normalization, computes cheap scores for every same-label pair, marks candidate pairs with the rules that produced them, writes deterministic merges to the merge log, rebuilds the alias map with `replay()`, and writes a run report. No LLM call yet. It works today on NVDA run 1's data (spec: Implementation Decisions, The `resolve` package and Data contracts; user stories 24–39, 50, 54–60, 79, 81).

This ticket also fixes the shared shapes that tickets 03 and 04 build on in parallel: the pair decision record, the `Resolution` result of `resolve_nodes(nodes, embedder, adjudicator, config)`, the run report's sections (one for adjudication, one for labels and scoring, left empty here), and the frozen config object with its hash.

**Blocked by:** None (can start immediately). Runs in parallel with 01.

**Parallel-safety:** touches a new `resolve/` package, a batched path in `ingest/voyage.py`, `pyproject.toml`/`uv.lock` (rapidfuzz) and `git_state()`'s +dirty list. Does not touch `graph/`, `extract/` or prompts.

**Status:** ready-for-agent

- [ ] Normalization: NFKC, casefold, punctuation and whitespace collapsed, then a hand-written legal-suffix list stripped repeatedly from the end, never to an empty string. Cases: "Groq, Inc." and "Groq"; "Huawei Technologies Co. Ltd." and "Huawei Technologies"; "Computershare Trust Company, N.A."; a bare "Company". Stored names are never changed.
- [ ] Filer table: hand-written names and aliases of NVIDIA, AMD and Intel mapped to CIK. An Organization whose match key is in the table merges into that Company with method `deterministic`, recording the table entry.
- [ ] Exact match after normalization within a label merges with method `deterministic`.
- [ ] Candidate rules over every same-label pair, each recorded on the pair: RapidFuzz `token_set_ratio` >= cutoff (90 to start); acronym (GDPR / General Data Protection Regulation, OBBBA / One Big Beautiful Bill Act); a shared rare word; top-k Voyage neighbours. MetricValues pair only within one filing on equal subject, period, value and unit. Each rule tested firing alone. RiskFactor is not paired.
- [ ] Batched Voyage embedding path with the same `input_type` for both sides, behind the existing disk cache keyed by model and text hash. The embedded text form ("label: name" or name plus one span) is a config choice. Tests use recorded vectors; a cached run makes zero calls.
- [ ] Without an adjudicator (ticket 03), candidate pairs that are not deterministic merges are reported as pending, never merged.
- [ ] Merge log: append-only JSONL, `merge` and `split` events with method, rule or decision ID, deciding evidence with chunk IDs, config hash, commit and timestamp. `replay(log)` gives the alias map. Tests: merge then split restores both keys; replay is the same every time; a split of an unknown merge is rejected; a rerun on unchanged inputs appends nothing.
- [ ] Canonical key: the member with the most evidence chunks, ties to the smallest key.
- [ ] Outputs under `benchmarks/resolution/`: merge log, alias map, run report with provenance (commit, config hash, ontology version, embedding model), counts by label, by rule and pending. A test checks that the committed alias map equals `replay()` of the committed log.
- [ ] `rapidfuzz==3.14.6` is a direct dependency (approved). `resolve/` added to `git_state()`'s +dirty list.
- [ ] `resolve.run` exit codes: 0, 2 bad input, 3 could not run (missing `VOYAGE_API_KEY` when embeddings are uncached), 4 checks failed.
- [ ] Rule tests written first and shown failing (`/tdd`). Code review with `mattpocock-skills:code-review`. Ruff and pytest pass, CI is green. BUILD-LOG entry appended with the counts on run 1.
