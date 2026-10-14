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

## Step 13 — router and benchmark
- `benchmarks/results.md`: ARCHITECTURE.md says CI regenerates it. Deferred at the Step 6 grilling and again at Step 11's (2026-10-14): decide whether a local command or CI writes it when the full matrix runs.
- Path context for graph arms: rendered paths in a new answer prompt, path-level citations and the path precision metric (ADR-0007 leaves them out of Step 11).
- Extending the quality gate to graph arms: a Neo4j snapshot in CI and a Cypher validity floor.

## After Step 8
- Haiku 5.5 against Sonnet 5.5 extraction quality is not compared: Step 9 switched the extractor to Haiku for cost (2026-10-10) and round 3 judges Haiku with prompt v2 only. A comparison would need a Sonnet run under v2 and its own review round.

## After Step 10 — graph load
- Entity-resolution recall. Step 9b's group cut raised recall from 0.387 to 0.783 [0.600, 0.947] (scoring round 2, agent-labelled, different draws; precision 1.000). Still open: the 5 round-2 misses are adjudicator answers (`distinct`, `related`, `unsure`), two of them on credit-facility labels that look wrong; round 1's 4 `unsure` misses and 3 near-stratum matches the rules never found. A change to the adjudicator prompt, `unsure` handling or the candidate rules would be a new tuning round and a fresh scoring draw.
- Whether to send more than 3 evidence chunks per node to the adjudicator (`MAX_CHUNKS_PER_NODE`): not measured in Step 9.
- Intel chunk `0000050863-24-000076:0017` failed extraction at `max_tokens` 16,000; extracting it again needs streaming for a higher cap. The load report and README list it as a gap.
- Incremental loads. The loader rebuilds the whole graph in one transaction (ADR-0004, amendment of 2026-10-14), which took 12 s at 24 filings. A larger corpus would need replace-in-place in Cypher: a per-filing delete that removes the filing's chunk IDs from edge lists and drops edges left without evidence.
- Persistence recall is 0.428 (agent-labelled), and almost half the misses are `same` answers dropped by the one-predecessor rule. Whether to allow several predecessors, or send ranks 4–6 to the adjudicator: revisit if Step 11's period questions need it.

## Lookup arm
- Map `MetricValue.concept` to XBRL tags from EDGAR companyfacts. Not done in Step 9 (grilling, 2026-10-10): MetricValue is per filing and never merges across filings. It is a new EDGAR data source; propose it when a lookup arm needs it.

## Step 11 — graph retrieval arms
- Haiku 5.5 as the Cypher generator, as an ablation against Sonnet 5.5.
- A filter on `uncertain` evidence, as an ablation, if wrong-evidence rates point at it. The reviews found no error at any confidence level, but only 3 counted `uncertain` samples.

## Step 13b — agentic arms
- Set the tool-call cap before the benchmark run.

## Paper
- Decide on a UCSD faculty co-author.
- Optional: Wayfinder for planning the paper and agentic arms.