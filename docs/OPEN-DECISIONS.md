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

## After the first graph arm
- `benchmarks/results.md`: ARCHITECTURE.md says CI regenerates it. Deferred at the Step 6 grilling (2026-10-06): decide whether a local command or CI writes it once there is more than one arm to compare.

## Step 8 — extraction
- Reject any fact whose evidence span doesn't appear verbatim (whitespace-normalised) in its chunk; report the rejection rate.

## Step 11 — graph retrieval arms
- Neo4j Community has no read-only roles (docs/research/neo4j-driver-and-constraints.md), so GRAPH-LAYER.md's "read-only credentials" are not available. Decide the guards for text-to-Cypher: READ transactions, rejecting any generated query whose `EXPLAIN` type is not read-only, and an allowlist of Cypher clauses.

## Step 13b — agentic arms
- Set the tool-call cap before the benchmark run.

## Paper
- Decide on a UCSD faculty co-author.
- Optional: Wayfinder for planning the paper and agentic arms.