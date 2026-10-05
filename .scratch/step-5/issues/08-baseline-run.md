# 08: Baseline run

**What to build:** the committed "before" number. The agent estimates the API cost from current prices, the user approves it, then the user runs `ingest.embed` and `eval.run --arm vector` on dev.

**Blocked by:** 04, 06, 07, 10 (the judge switch to gpt-6-luna and its spot check)

**Status:** done (2026-10-05)

- [x] Cost estimate from current Voyage and Anthropic prices, approved by the user before the run (2026-10-05: about $28–42 per full run, $55–85 for the baseline plus the repeat)
- [x] Cost estimate redone with the judge chosen in ticket 10 and approved by the user before the run (the 2026-10-05 estimate assumed the Opus judge). Approved 2026-10-05: about $3.20 for the baseline and $3.50 for the uncached repeat, $5–10 with margin, from the spot check's measured $0.030 per answer and $0.0073 per question for 3 luna `medium` judge runs
- [x] 2,144 rows in chunk_embeddings (checked 2026-10-05)
- [x] filter-excluded-gold is 0 on dev (the test, and the baseline run's own filter report)
- [x] One uncached repeat run of the dev questions, with the score change between the two runs recorded (BUILD-LOG, 2026-10-05)
- [x] The baseline results file is committed with its commit and config (`benchmarks/runs/2026-10-05-vector-dev-96e8c69.json`)
- [x] BUILD-LOG entry records the multi-hop number as the "before", labelled "agent-drafted questions" and "uncalibrated" where judged
