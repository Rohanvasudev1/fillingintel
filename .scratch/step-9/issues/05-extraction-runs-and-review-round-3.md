# 05: Extraction runs and review round 3

**What to build:** the extracted corpus. With the user's go-ahead before each paid run: NVDA FY2026 10-K again under `extract/v2`, then AMD FY2025 10-K. The user judges review round 3 on AMD in the Terminal panel. If round 3 scores about 85% or better, the other 21 filings are extracted; if not, the prompt goes back for a fix first (spec: Solution, Part 1; user stories 13–23).

**Blocked by:** 01. Runs in parallel with 03 and 04.

**Parallel-safety:** no code changes. Writes only run outputs (gitignored `data/extract/`) and committed reports and round files under `benchmarks/extraction/`.

**Status:** ready-for-human

- [ ] NVDA FY2026 run under v2: the run report shows market platforms as MarketPlatform nodes and only Compute & Networking and Graphics as Segments, with zero `check_batch()` violations.
- [ ] AMD FY2025 10-K run, report committed.
- [ ] Before round 3, the agent explains what checking means and the review standard. The user runs `python -m extract.review` from the main checkout in the Terminal panel (exact commands given). Afterwards the agent checks the answer timings.
- [ ] Round 3 results committed: accuracy with a Wilson 95% interval, accuracy per confidence level, median time per answer, and the miss rate from the miss check. An agent second look, if any, is labelled as such and changes no verdict.
- [ ] If round 3 is under about 85%: stop, report the failure reasons, and propose a prompt fix (a new prompt version, through a ticket-sized PR) before any further run.
- [ ] The other 21 filings extracted, each with `--max-cost` at $10, the total kept within $40. Commands run with `env -u ANTHROPIC_BASE_URL`.
- [ ] All 24 run reports committed; total cost, rejections by reason and conflicts summed in BUILD-LOG.
