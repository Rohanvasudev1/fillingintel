# 10: Judge spot check

**What to build:** `python -m eval.spotcheck` judges 10 fixed dev questions with `gpt-6-luna` at effort `medium` and `high` and with `gpt-6-sol` at `medium` as the reference judge, reusing the cached answers, and reports pass or fail per luna effort against thresholds fixed before any result is seen. The live run decides which judge model and effort ticket 08 uses. Source: the 2026-10-05 amendment in the Step 5 spec.

**Blocked by:** 09

**Status:** ready-for-agent

- [ ] A pure comparison function takes two judges' results on the same questions and returns, per metric, verdict agreement (citation support, decline and not-found correctness) or mean absolute gap (faithfulness, answer relevancy), with a pass flag; tests with hand-built results cover exact agreement, 85% exactly and just under, a 0.10 gap exactly and just over, missing scores, and mismatched question sets refused (written failing first)
- [ ] The thresholds (agreement of at least 85%, gap of at most 0.10) are constants matching the spec and are not changed after the run
- [ ] The 10 questions are chosen by hash from the dev split and spread across classes; the selection is deterministic (test against the dev records)
- [ ] The command reuses cached answers (no new answer calls) and writes its comparison to a results file that is not committed
- [ ] Cost estimate shown to the user and approved before the live run (expected under $1)
- [ ] The live spot check is run once; the cheaper luna effort that passes both rules becomes the judge config, or `gpt-6-sol` at `medium` if neither passes (a config change only)
- [ ] If luna's real output-token use allows, the output cap is lowered, recorded as a config change
- [ ] `mattpocock-skills:code-review` findings fixed or explained; `uv run ruff check .` and `uv run --env-file .env pytest` pass
- [ ] BUILD-LOG entry with the per-metric numbers, the commit, the chosen judge and effort, and the label "spot check, not calibration"
