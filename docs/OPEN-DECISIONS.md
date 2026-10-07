# Open decisions

Decisions raised but not yet made, grouped by the step where each must be settled. When one is resolved, record it in CLAUDE.md Decisions and delete it here.

## After Step 3
- Switch model: strongest available for planning and reviews, Sonnet-tier for implementation. Step 3 planning ran on Opus 5.5.
- Optional: pstack /arena for design comparisons at Steps 7 and 9.

## Step 4 — eval set and research design
- Optional, at any point: review agent drafts with `python -m eval.review`, or write human questions. Any human records are reported in their own column next to the agent-drafted results.
- Optional, before the final benchmark: the friend Ctrl-F test on five dev multi-hop drafts (skipped on 2026-10-04).

## Steps 5–6 — eval harness
Done in Step 5: judge model and effort pinned, 3 judge runs per answer, bootstrap 95% intervals on every results cell. Still open:
- Calibrate the LLM judges against ~60 hand-scored answers (Cohen's kappa). Until then every judged number is labelled "uncalibrated". Must happen before the final benchmark.
- Paired comparisons between arms, once a second arm exists (Step 11).

## Step 6 — to confirm (raised during the tickets)
- The gate loads the committed snapshot itself, into a throwaway schema it drops afterwards, rather than CI loading it in a separate step (ticket 03). Recommendation: confirm. The laptop and CI then run the same command.
- The gate's question set hash covers only the 82 gated records, so editing a decline or `test` record doesn't force a baseline rewrite (ticket 03). Recommendation: confirm.
- Judge prompt text never goes on spans, even with `FILINGINTEL_TRACE_TEXT=true` (ticket 08). Recommendation: keep it off. The prompts repeat chunk and answer text already on the answer span, and they're large.
- Ruleset 24576386 has no "require a pull request" rule. A commit that already passed both checks on a branch can still be pushed straight to `main` (ticket 05). Recommendation: add the rule. It's a settings change, so it needs your yes to the exact `gh api` call.

## After the first graph arm
- `benchmarks/results.md`: ARCHITECTURE.md says CI regenerates it. Deferred at the Step 6 grilling (2026-10-06): decide whether a local command or CI writes it once there is more than one arm to compare.

## Step 7 — ontology
- Add Supplier and Competitor node types so cross-company questions are answerable by traversal.

## Step 8 — extraction
- Reject any fact whose evidence span doesn't appear verbatim (whitespace-normalised) in its chunk; report the rejection rate.

## Step 13b — agentic arms
- Set the tool-call cap before the benchmark run.

## Paper
- Decide on a UCSD faculty co-author.
- Optional: Wayfinder for planning the paper and agentic arms.