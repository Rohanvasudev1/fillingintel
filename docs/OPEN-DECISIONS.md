# Open decisions

Decisions raised but not yet made, grouped by the step where each must be settled. When one is resolved, record it in CLAUDE.md Decisions and delete it here.

## After Step 3
- Switch model: strongest available for planning and reviews, Sonnet-tier for implementation. Step 3 planning ran on Opus 5.5.
- Optional: pstack /arena for design comparisons at Steps 7 and 9.

## Step 4 — eval set and research design
- Optional, at any point: review agent drafts with `python -m eval.review`, or write human questions. Any human records are reported in their own column next to the agent-drafted results.
- Optional, before the final benchmark: the friend Ctrl-F test on five dev multi-hop drafts (skipped on 2026-10-04).

## Steps 5–6 — eval harness
Done in Step 5: judge model and effort pinned, 3 judge runs per answer, bootstrap 95% intervals on every results cell. Judge calibration is scheduled in Step 13 (CLAUDE.md Decisions).

## After Step 13 — router and benchmark
- Path context for graph arms: rendered paths in a new answer prompt, path-level citations and the path precision metric (ADR-0007 leaves them out of Step 11; kept out of Step 13 at its grilling, since adding them now would tune the graph arms after their loss).
- Extending the quality gate to graph arms: a Neo4j snapshot in CI and a Cypher validity floor.
- graph_local's dev–`test` gap on local questions (recall@10 0.909 on dev, 0.500 on `test`, n 13). The arm stays as frozen; a look at which local questions it misses would be a dev-only ablation, never a change to the `test` rows.
- Replay credit in pre-run estimates reads `benchmarks/runs/`, which is gitignored, so an uncommitted local run there counts as committed (as `eval/step13_spend.py` already does). Fine while every file there is force-added; a later step that keeps local runs should name its inputs instead of globbing.
- Router latency on `test` is replay time, since both router runs replayed every arm answer. An end-to-end router latency needs an `--uncached` router run (about $6 at `test` prices) or a sample of it.
- Judge calibration deferred (user decision, 2026-10-19): the user did not score the 30 drawn answers, so every judged number stays "uncalibrated". To run it later, nothing needs building: `uv run python -m eval.calibrate --reviewer "<name>"` (about 1.5–2 hours for 425 claims), then `python -m eval.calibration.report` and `python -m eval.results_table`. Options when reopened: score all 30; score fewer and report them as directional (a research-plan amendment); or a later, larger calibration (about 60–100 answers).

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
- Risk factors without a body (ticket 01, load run 4): 257 of 1,405 have no span in their filing's risk-factor section (most were extracted from Item 1 or MD&A), and 371 are sentences from inside a body, so they get no body of their own. Whether body-sentence factors should merge into the factor whose body holds them, or be dropped at extraction, is open. Decide if `graph_local` anchors on them poorly.
- Extraction spend caps count committed spend per ontology version (`extract/spend.py`). Under ontology 4 they see none of the ontology 3 runs, so a new extraction run would start from $0 against the caps. Nothing is extracted in Step 11; fix before the next extraction.
- Hops in the run header (ticket 05): `ArmConfig` has no hop field, so `graph_local`'s frozen hop count appears only per question. Recommendation: leave it; adding a field changes the header schema.
- A filter on `uncertain` evidence, as an ablation, if wrong-evidence rates point at it. The reviews found no error at any confidence level, but only 3 counted `uncertain` samples.

## Step 12 — communities and global search
- Ablations not run in Step 12 or 13: Haiku 5.5 for map calls; a cheap model rating reports (GraphRAG's dynamic community selection) instead of embedding rank.

## Step 13b — agentic arms
- Set the tool-call cap before the benchmark run.
- Whether to use LangGraph for the agentic loops (not adopted in Step 13). If used: set `recursion_limit` explicitly (the installed default is 10,007) and count tool calls in state.

## Paper
- Decide on a UCSD faculty co-author.
- Optional: Wayfinder for planning the paper and agentic arms.