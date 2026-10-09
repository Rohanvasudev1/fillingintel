# 05: Stop condition and close

**What to build:** the Step 8 result. The agent runs the full extraction of the NVDA FY2026 10-K (0001045810-26-000021) after the user approves its cost estimate; the user reviews round 1; if it scores under 26 of 30, the agent writes prompt v2 from the wrong-answer reasons and misses, reruns the whole filing, and the user reviews a fresh round. Every run and round is reported (RUNBOOK Step 8 stop condition and its 2026-10-09 note).

**Blocked by:** 03 (`python -m extract.run`), 04 (`python -m extract.review`)

**Status:** ready-for-human

- [ ] The agent shows the cost estimate and waits for the user's yes before the run.
- [ ] The run covers every chunk with no failed chunks, and `check_batch()` reports zero violations on the whole filing.
- [ ] Before each round, the agent explains what checking a triple means (right labels, direction, properties, a span that states it; partly right is wrong) and the expected time, about 35 minutes. Afterwards it reports the round's median answer time.
- [ ] The last round scores at least 26 of 30. Every round is reported with its count, Wilson interval, accuracy per confidence level and miss rate, including failed rounds.
- [ ] Each prompt revision is a new prompt file; earlier versions stay.
- [ ] Run reports and round files are committed. BUILD-LOG gets a dated entry with the runs, rounds, rejection rates by reason, conflicts, cost and what each prompt revision changed, ending with "Next session starts with". The note says the local Neo4j's version-1 `:GraphMeta` must be cleared by the user at the start of Step 10.
- [ ] MEMORY and CLAUDE.md are updated if anything was decided along the way.
