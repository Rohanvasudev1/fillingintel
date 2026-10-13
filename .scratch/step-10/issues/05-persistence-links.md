# 05: Persistence links: candidates, adjudication, selection

**What to build:** Each risk factor in a 10-K links to the same risk in the company's previous 10-K, and each risk factor in a 10-Q to the same risk in the most recent 10-K before it. Spec: `.scratch/step-10/spec.md` (Persistence linking, Persistence link evidence). Vocabulary: CONTEXT.md (persistence link).

**Blocked by:** None (can start immediately)

**Status:** done

- [x] Filing pairs: same company, 10-K to previous 10-K and 10-Q to most recent earlier 10-K, within the corpus (12 pairs). Filings with no earlier 10-K in the corpus get no links.
- [x] Identical normalized titles in a pair link with no LLM call, method `persist/exact_title`.
- [x] Other risk factors get their top 3 Voyage neighbours among the earlier 10-K's risk factors, embedding "title plus span" through the cached entity-embedding path.
- [x] Prompt `prompts/persist/v1.md`, pinned by hash: Haiku 5.5, effort high, structured output, response cache; answers `same`, `distinct`, `unsure` with a quote from each side, checked like Step 8 spans. Only `same` with verified quotes links.
- [x] A later risk factor has at most one predecessor (exact match, else highest-ranked `same`); an earlier one may have several successors.
- [x] Each link's evidence: one chunk and span from each risk factor (earlier first), confidences `stated`, extract-prompt entry the method string; the link passes `check_batch()` as a PERSISTS_AS edge.
- [x] The run checks ANTHROPIC_API_KEY and VOYAGE_API_KEY, refuses when ANTHROPIC_BASE_URL is set, shows its estimate and refuses over the $2 cap.
- [x] Test-first with a fake adjudicator and fake embeddings; a few real replies recorded for a recorded test.
- [x] The persistence module's directory is added to `git_state()`'s dirty list.
- [x] The real run (about $1) waits for the user's go-ahead; link file and run report committed under `benchmarks/persistence/` with provenance, counts by method, decisions, tokens and cost.
- [x] `mattpocock-skills:code-review`; ruff and pytest green.
