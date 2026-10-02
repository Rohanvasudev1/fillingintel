# Open decisions

Decisions raised but not yet made, grouped by the step where each must be settled. When one is resolved, record it in CLAUDE.md Decisions and delete it here.

## Before Step 3 — setup session
- Upgrade ECC to the current ecc@ecc plugin (single install method); update command names in CLAUDE.md.
- Switch model: strongest available for planning and reviews, Sonnet-tier for implementation. Log the model and version in BUILD-LOG.
- Install Matt Pocock's code-review and grilling skills at project scope; run the context-budget check before and after.
- Optional: pstack /arena for design comparisons at Steps 7 and 9.

## Step 2b — close out
- Full-corpus parser run (~24 filings): required sections present, extraction method per section, section lengths. Position rules (90%, last 20%, 15%) must generalise beyond the 6 fixtures.
- Merge docs/BUILD_LOG.md into docs/BUILD-LOG.md; delete the underscore file.

## Step 3 — chunking
- Split into sub-checkpoints sized to one session: 3a table schema, 3b chunker, 3c Postgres load and resolve().

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