# Step 4 grilling session: decisions for the user

These are the questions to settle with the user. Each has the assistant's recommendation, so the session can be short.

1. **Approve the hypotheses** in `.claude/plans/research-plan-draft.md`, or change them. In particular, set the thresholds: recall within 0.05 (H1), +0.10 (H2), +0.10 (H4), 2x latency (H5), 80% router accuracy (H6). Recommendation: keep them as drafted, so they're dated and fixed before any run.
2. **Move the draft to `docs/RESEARCH-PLAN.md`.** That needs a one-time approval past the ECC hook, or an allowlist change for `docs/`. Recommendation: allow `docs/` in the hook, because the project keeps its specs there.
3. **Settled 2026-10-04: the user chose the agent-drafted set as the benchmark set, without human review, labelled as agent-drafted everywhere.**
4. **(Moot unless the user reviews later.) How to review the drafts.** Recommendation: do it in sessions of 20 with `python -m eval.review`. Reject anything you wouldn't have asked, then edit accepted records in `eval/eval_set.jsonl` freely.
5. **The friend test.** The RUNBOOK asks a friend to try five multi-hop questions with Ctrl-F. Recommendation: do it after review, on `dev` multi-hop records only.
6. **Class counts.** The RUNBOOK targets 40/35/30/15, and the plan adds about 10 `decline` and about 10 `unanswerable`. Recommendation: keep the RUNBOOK's 120 for the four main classes, and treat the 20 edge cases as extra, outside the 120.
7. **The FinRank external check** (Steps 5–6 proposal). Recommendation: yes, after the vector baseline, as an appendix result.
8. **Setup items** (OPEN-DECISIONS "After Step 3"): the ECC upgrade, the model switch, and the `grilling` skill. Recommendation: upgrade ECC before Step 5. Switch the model in app settings: Opus for planning and reviews, Sonnet for implementation. Remove `grill-me` if the `grilling` skill stays uninstalled.

## Outcome (2026-10-04 grilling session)
1. Approved, with three changes: H2 covers all `multi_hop` questions, with cross-company as a secondary breakdown; classes with fewer than 10 `test` questions are reported as "directional"; H6 carries a fixed class-to-arm map. Thresholds kept as drafted.
2. Moved with `git mv` to `docs/RESEARCH-PLAN.md`. The hook lives in the ECC 1.4.1 plugin cache, so the `docs/` allowlist change waits until after the ECC upgrade.
5. Skipped for now; optional before the final benchmark.
6. 120 in the four main classes, plus 20 extra.
7. Yes, as an appendix after the vector baseline.
8. See the BUILD-LOG entry for this date.
