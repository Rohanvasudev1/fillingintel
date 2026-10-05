# 05: Protect main and run the stop condition

**What to build:** `main` is protected by a GitHub ruleset that requires the `lint-and-test` and `quality-gate` checks, with no bypass actors, so a PR that weakens retrieval cannot merge. Then the RUNBOOK's stop condition: a PR that sets the vector arm's k to 1 shows a red `quality-gate` check and cannot merge, and is closed. The `gh api` call shape is in docs/research/phoenix-tracing.md section 5.

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] Before creating the ruleset, the agent shows the user the exact `gh api` call and waits for a yes in chat.
- [ ] The ruleset targets `main`, requires `lint-and-test` and `quality-gate`, and has no bypass actors. Read it back with `gh api` and confirm.
- [ ] A direct push to `main` is rejected (shown by the push error or the ruleset's evaluation, without forcing anything).
- [ ] A branch that sets k to 1 is opened as a PR; `quality-gate` fails, naming the dropped numbers, and GitHub shows the PR as blocked.
- [ ] The k = 1 PR is closed without merging and its branch deleted; its link goes in the BUILD-LOG entry.
- [ ] The BUILD-LOG entry records that v1 (RUNBOOK steps 1–6) is complete once this ticket and the tracing tickets are done.
