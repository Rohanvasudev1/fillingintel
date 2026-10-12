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

## After Step 8
- Haiku 5.5 against Sonnet 5.5 extraction quality is not compared: Step 9 switched the extractor to Haiku for cost (2026-10-10) and round 3 judges Haiku with prompt v2 only. A comparison would need a Sonnet run under v2 and its own review round.
- Whether retrieval filters out `uncertain` evidence: decide in Step 11 from the review's accuracy per confidence level.

## Step 10 — graph load
- MetricValue keys collide when a percentage and a dollar figure share a concept name. Haiku with prompt v2 stored NVIDIA's "net income" as 55.6 percent (its share of revenue) and as $120,067 million under one key, and the conflict rule kept the percentage; 34 of NVDA run 2's conflicts and 32 of AMD run 1's 35 are this kind. AMD's "income tax (benefit)" FY2025 holds the effective rate, -2.5 percent (agent second look at round 3). The extract files keep each unit, so the loader can separate a percentage from a currency amount when it builds the key, with no re-extraction (user decision 2026-10-11: run the corpus under v2 and fix this in code). Decide the rule at Step 10's grilling: unit class in the key, or a percent concept renamed.

- Entity-resolution recall (Step 9 scored 0.387, agent-labelled). Five of the nine missed candidate matches were `same` answers held back because their group had more than 5 names or held a `related` / `distinct` pair. The user chose (2026-10-12) to split held-back groups instead of dropping them, as Step 9b before Step 10 (RUNBOOK). Settled at the Step 9b grilling (2026-10-12, CLAUDE.md Decisions): minimum group cut with tied keys left unmerged, a 12-name limit for every label, round 2 reported beside round 1 on a fresh draw. Open until round 2 is scored: whether the 4 `unsure` misses and the 3 near-stratum matches need their own change.
- Whether to send more than 3 evidence chunks per node to the adjudicator (`MAX_CHUNKS_PER_NODE`): not measured in Step 9.
- Intel chunk `0000050863-24-000076:0017` failed extraction at `max_tokens` 16,000; extracting it again needs streaming for a higher cap. Two Intel MetricValues lost their MEASURES edge in extraction and are skipped by resolution; the loader decides whether they load.

## Lookup arm
- Map `MetricValue.concept` to XBRL tags from EDGAR companyfacts. Not done in Step 9 (grilling, 2026-10-10): MetricValue is per filing and never merges across filings. It is a new EDGAR data source; propose it when a lookup arm needs it.

## Step 11 — graph retrieval arms
- Neo4j Community has no read-only roles (docs/research/neo4j-driver-and-constraints.md), so GRAPH-LAYER.md's "read-only credentials" are not available. Decide the guards for text-to-Cypher: READ transactions, rejecting any generated query whose `EXPLAIN` type is not read-only, and an allowlist of Cypher clauses.

## Step 13b — agentic arms
- Set the tool-call cap before the benchmark run.

## Paper
- Decide on a UCSD faculty co-author.
- Optional: Wayfinder for planning the paper and agentic arms.