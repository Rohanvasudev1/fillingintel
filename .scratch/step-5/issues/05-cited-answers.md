# 05: Cited answers

**What to build:** the vector arm writes an answer with `claude-sonnet-5-5` at effort `high` from all 10 retrieved chunks, using answer prompt version 1. A deterministic step keeps only sentences that cite retrieved chunk IDs, and the harness reports structural citation validity, dropped sentences, and latency and cost per stage.

**Blocked by:** 03

**Status:** ready-for-agent

- [x] Adds the `anthropic` dependency (approved). ANTHROPIC_API_KEY added empty to .env.example
- [x] No temperature, top_p or top_k is sent. Effort is set explicitly and recorded
- [x] A disk cache keyed by model, effort and prompt hash stores responses, so a rerun makes no new answer calls
- [x] Answer prompt is a versioned file. The header records its version and SHA-256
- [x] The prompt asks for a `[chunk_id]` citation per sentence, declines buy/hold/sell and price-target questions, and says when the retrieved filings lack the evidence
- [x] Citation enforcement drops sentences with no citation or with a citation outside the retrieved chunks, keeps them in the record, and counts them
- [x] Structural citation validity per cell
- [x] Cost from the API's reported token counts and a dated price table in config. p50 and p95 latency per stage
- [x] Tests with a fake answer model replaying recorded responses, including a decline, a not-found answer and an answer with uncited sentences (real responses recorded 2026-10-05)
- [x] Reviewed with `mattpocock-skills:code-review`, every finding fixed or explained
- [x] `uv run ruff check .` and `uv run --env-file .env pytest` pass
