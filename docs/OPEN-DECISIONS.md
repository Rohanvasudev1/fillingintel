# Open decisions

Decisions raised but not yet made, grouped by the step where each must be settled. When one is resolved, record it in CLAUDE.md Decisions and delete it here.

## After Step 3
- Upgrade ECC to the current ecc@ecc plugin (single install method); update command names in CLAUDE.md. Run its context-budget check (not in ECC 1.4.1). Afterwards, add `docs/` to the hook that blocks new .md files (agreed 2026-10-04; an edit to the 1.4.1 plugin cache would be overwritten by the upgrade).
- Switch model: strongest available for planning and reviews, Sonnet-tier for implementation. Step 3 planning ran on Opus 5.5.
- Optional: pstack /arena for design comparisons at Steps 7 and 9.

## Step 4 — eval set and research design
- Optional, at any point: review agent drafts with `python -m eval.review`, or write human questions. Any human records are reported in their own column next to the agent-drafted results.
- Optional, before the final benchmark: the friend Ctrl-F test on five dev multi-hop drafts (skipped on 2026-10-04).

## Steps 5–6 — eval harness
- Calibrate LLM judges against ~60 hand-scored answers (Cohen's kappa); pin judge model versions; average 3 runs.
- Bootstrap confidence intervals on every results cell; paired comparisons between arms.

## Step 7 — ontology
- Add Supplier and Competitor node types so cross-company questions are answerable by traversal.

## Step 8 — extraction
- Reject any fact whose evidence span doesn't appear verbatim (whitespace-normalised) in its chunk; report the rejection rate.

## Step 13b — agentic arms
- Set the tool-call cap before the benchmark run.

## Paper
- Decide on a UCSD faculty co-author.
- Optional: Wayfinder for planning the paper and agentic arms.