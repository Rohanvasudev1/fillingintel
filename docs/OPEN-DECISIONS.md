# Open decisions

Decisions raised but not yet made, grouped by the step where each must be settled. When one is resolved, record it in CLAUDE.md Decisions and delete it here.

## After Step 3
- Switch model: strongest available for planning and reviews, Sonnet-tier for implementation. Step 3 planning ran on Opus 5.5.
- Optional: pstack /arena for design comparisons at Steps 7 and 9.

## Step 4 — eval set and research design
- Optional, at any point: review agent drafts with `python -m eval.review`, or write human questions. Any human records are reported in their own column next to the agent-drafted results.
- Optional, before the final benchmark: the friend Ctrl-F test on five dev multi-hop drafts (skipped on 2026-10-04).

## Steps 5–6 — eval harness
- Calibrate LLM judges against ~60 hand-scored answers (Cohen's kappa); pin judge model versions; average 3 runs.
- Bootstrap confidence intervals on every results cell; paired comparisons between arms.
- Embeddings on reload (raised 2026-10-05, ticket 02). Rerunning `ingest.load` deletes and reinserts each filing's chunks, and `ON DELETE CASCADE` drops their vectors, so the next `ingest.embed` re-embeds the whole corpus. Recommendation: keep the cascade, and later make `load_filing` skip a filing whose text hash and chunk offsets are unchanged. The alternative, `ON DELETE RESTRICT` with an explicit delete, only moves the same step elsewhere.
- Embed batch size 1 (raised 2026-10-05, ticket 02). The spec says "batches"; one chunk per request is the only way to store the token count Voyage reports for each chunk. Recommendation: accept it. The alternative is multi-text batches with only the batch total stored, which needs a schema change.

## Step 7 — ontology
- Add Supplier and Competitor node types so cross-company questions are answerable by traversal.

## Step 8 — extraction
- Reject any fact whose evidence span doesn't appear verbatim (whitespace-normalised) in its chunk; report the rejection rate.

## Step 13b — agentic arms
- Set the tool-call cap before the benchmark run.

## Paper
- Decide on a UCSD faculty co-author.
- Optional: Wayfinder for planning the paper and agentic arms.