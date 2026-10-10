# 06: Tune, freeze, score, close Step 9

**What to build:** the published entity-resolution number. Over all 24 extracted filings: label the tuning set, tune the candidate cutoffs, k, the embedded text and the adjudicator prompt on it, freeze them, run the resolver, then draw, label and score the 100 scoring pairs once. The figures go into the README with commit and config, and Step 9 closes in BUILD-LOG (spec: Solution, Part 2 and Scoring; user stories 66–74; RUNBOOK Step 9 stop condition).

**Blocked by:** 03, 04, 05.

**Status:** ready-for-agent (paid runs need the user's go-ahead)

- [ ] Tuning set drawn and labelled. Cutoffs, k, embedded text and prompt chosen on it; the choices and their tuning numbers recorded in the run report. Any prompt edit creates a new version.
- [ ] Config and prompt hashes frozen and recorded before the scoring draw.
- [ ] `resolve.run` over all 24 outputs: merge log, alias map and report committed; held-back groups and related pairs listed.
- [ ] Scoring set drawn, labelled and scored once. No tuning change after the scoring set has been seen; if one is needed, it is reported as a new scoring round with a fresh draw.
- [ ] README states pairwise precision and recall with intervals, adjudicator accuracy, the rules' share of true matches, raw counts per stratum, the commit and config, and "agent-labelled (gpt-6-luna)", with one sentence saying the RUNBOOK asked for hand labels and the user chose an agent labeller.
- [ ] RUNBOOK Step 9 note and CLAUDE.md Decisions updated with anything settled during tuning; OPEN-DECISIONS updated.
- [ ] BUILD-LOG entry closing Step 9 with costs, the numbers, regressions if any, and "Next session starts with" Step 10 (including clearing the local v1 `:GraphMeta`).
- [ ] Code review with `mattpocock-skills:code-review` on any code changed. Ruff and pytest pass, CI is green.
