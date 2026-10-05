# 08: Baseline run

**What to build:** the committed "before" number. The agent estimates the API cost from current prices, the user approves it, then the user runs `ingest.embed` and `eval.run --arm vector` on dev.

**Blocked by:** 04, 06, 07

**Status:** ready-for-human

- [x] Cost estimate from current Voyage and Anthropic prices, approved by the user before the run (2026-10-05: about $28–42 per full run, $55–85 for the baseline plus the repeat)
- [x] 2,144 rows in chunk_embeddings (checked 2026-10-05)
- [ ] filter-excluded-gold is 0 on dev
- [ ] One uncached repeat run of the dev questions, with the score change between the two runs recorded
- [ ] The baseline results file is committed with its commit and config
- [ ] BUILD-LOG entry records the multi-hop number as the "before", labelled "agent-drafted questions" and "uncalibrated" where judged
