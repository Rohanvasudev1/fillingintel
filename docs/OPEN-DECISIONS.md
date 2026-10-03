# Open decisions

Decisions raised but not yet made, grouped by the step where each must be settled. When one is resolved, record it in CLAUDE.md Decisions and delete it here.

## After Step 3
- Upgrade ECC to the current ecc@ecc plugin (single install method); update command names in CLAUDE.md. Run its context-budget check (not in ECC 1.4.1).
- Switch model: strongest available for planning and reviews, Sonnet-tier for implementation. Step 3 planning ran on Opus 5.5.
- Install the `grilling` skill that `grill-me` calls, or remove `grill-me`.
- Optional: pstack /arena for design comparisons at Steps 7 and 9.

## Step 4 — eval set and research design
- FinRank as the main human-written question set, if its license allows research use. Own set shrinks to ~60 cross-period and cross-company questions.
- 1–2 hand-picked hard negatives per question; report a wrong-evidence rate.
- Reword invariant 1: questions are human-written or human-verified and labelled as such; synthetic questions kept in a separate set and reported separately; agents never edit gold answers.
- Optional: AI-drafted lookup questions verified by hand; synthetic-vs-human ranking comparison as a paper result.
- Write docs/RESEARCH-PLAN.md with dated hypotheses before any benchmark run.

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