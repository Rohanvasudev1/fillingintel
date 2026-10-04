# Open decisions

Decisions raised but not yet made, grouped by the step where each must be settled. When one is resolved, record it in CLAUDE.md Decisions and delete it here.

## After Step 3
- Upgrade ECC to the current ecc@ecc plugin (single install method); update command names in CLAUDE.md. Run its context-budget check (not in ECC 1.4.1).
- Switch model: strongest available for planning and reviews, Sonnet-tier for implementation. Step 3 planning ran on Opus 5.5.
- Install the `grilling` skill that `grill-me` calls, or remove `grill-me`.
- Optional: pstack /arena for design comparisons at Steps 7 and 9.

## Step 4 — eval set and research design
- Approve `docs/RESEARCH-PLAN.md`: the draft is in `.claude/plans/research-plan-draft.md`. An ECC hook blocks new .md files in docs/, so moving it needs the user's go-ahead. Settle it in a grilling session, using the decision list in `.claude/plans/step-4-grilling.md`.
- Review the agent drafts (`python -m eval.review`) and write the remaining human questions until `eval/eval_set.jsonl` holds 120 records.

## Steps 5–6 — eval harness
- Proposal (not built): an external check of the vector arm on FinRank's pooled corpus (5,230 passages, CC BY-NC 4.0, github.com/datanxt/FinRank), against its published Recall@10 baselines. It adds a data source, so it needs the user's approval.
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