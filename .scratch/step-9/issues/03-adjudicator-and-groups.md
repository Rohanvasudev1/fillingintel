# 03: Adjudicator and groups

**What to build:** LLM adjudication of candidate pairs and turning decisions into merges. claude-sonnet-5-5 judges each pending candidate pair `same`, `related`, `distinct` or `unsure`, quoting both nodes' chunks; quotes are checked in code. `same` decisions form groups by union-find; a group with a contradiction or more than 5 names is held back; the rest merge with method `llm` (spec: Implementation Decisions, adjudicate and groups; user stories 40–53, 75, 77, 78, 80).

**Blocked by:** 02. Runs in parallel with 04 and 05.

**Parallel-safety:** touches the adjudication and grouping modules in `resolve/`, `prompts/resolve/`, and only the adjudication section of the run report that 02 set up.

**Status:** done (merged 2026-10-10)

- [ ] `prompts/resolve/v1.md`, pinned by hash, version `resolve/v1@<hash8>` recorded on every decision. It states the same-entity rule from CONTEXT.md: same thing at the same level; a family and its member differ; a parent and subsidiary merge only where the filing uses the names interchangeably.
- [ ] The request carries both names, the label, and each node's evidence spans and chunk text from `resolve()`. claude-sonnet-5-5 at effort `high`, structured output, no sampling settings, through the existing Anthropic client and response cache.
- [ ] Reply: `answer` in {same, related, distinct, unsure}; `related_kind` in {family_member, parent_subsidiary, variant} when related; a quote and chunk ID for each node; a reason. Each quote is checked against its own node's chunks with Step 8's span normalization (reusing extract's checker).
- [ ] Only `same` with both quotes verified merges. Counted by reason: quote not found, quote from the other node's chunks, `unsure`, `related` (by kind), `distinct`, invalid reply.
- [ ] Recorded real replies for run 1 pairs (Groq variants, Huawei, GDPR / U.K. GDPR, OBBBA, Teter, Blackwell / GB200, GeForce / GeForce NOW, Revenue / Total revenue), captured once after asking the user and replayed offline. Hand-written broken replies, one rule each.
- [ ] Groups: union-find over `same`. A group containing any `related` or `distinct` pair, or more than 5 names, is held back and listed in the report with every decision involved. Tests: A=B, B=C, A≠C held back; a 6-name chain held back; a clean 3-name group merged with the right canonical key.
- [ ] `resolve.run` checks `ANTHROPIC_API_KEY`, refuses when `ANTHROPIC_BASE_URL` is set, shows an estimate and refuses over `--max-cost` (exit 3). A cached run makes zero calls.
- [ ] Phoenix spans for adjudicator calls when tracing is on, with model, tokens, cost and cache hit; no prompt text unless `FILINGINTEL_TRACE_TEXT=true`.
- [ ] Config pins provider, model, effort and output cap; the dated price table is used for cost.
- [ ] Rule tests written first and shown failing (`/tdd`). Code review with `mattpocock-skills:code-review`. Ruff and pytest pass, CI is green. BUILD-LOG entry appended.
