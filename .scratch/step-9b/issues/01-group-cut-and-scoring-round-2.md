# 01: Group cut and scoring round 2

**What to build:** Cut held-back resolution groups into pieces and merge each piece that passes, instead of dropping the whole group. Check the change on the tuning set, freeze it, and score it once on a fresh 100-pair set ("scoring round 2"), shown in the README beside round 1. Spec: `.scratch/step-9b/spec.md`. Decisions: CLAUDE.md Decisions, Step 9b entry. Vocabulary: CONTEXT.md (held-back group, group cut, piece). Research: docs/research/er-cluster-splitting.md.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

Order of work: code test-first, then review and verification, then the paid run. The run stops for the user at the tuning check if any newly merged tuning pair is labelled not same.

### Group cut (test-first through resolve_nodes() with the scripted fake adjudicator)
- [ ] A held-back group is cut by removing the fewest `same` answers so that no `related` or `distinct` pair shares a piece, found by exact enumeration. Each piece stays connected.
- [ ] Deterministic merges are never cut. A group whose contradiction no cut can separate stays held back, with that reason.
- [ ] When smallest cuts tie, keys every tied cut places alike merge, and a key placed differently stays unmerged.
- [ ] `unsure` answers never cause a cut.
- [ ] The name limit is 12 for every label and applies to each piece; a larger piece stays held back, with its reason. The search is bounded so no group runs unbounded.
- [ ] The result does not depend on the order of the decisions.
- [ ] A group of 5 or fewer names with no contradiction merges exactly as in Step 9 (regression).
- [ ] The name limit and a named group rule are in `ResolveConfig` and change the config hash.
- [ ] Each piece is an `llm` merge event with rule `group_cut`, citing only the `same` answers kept inside it. The canonical-key rule is unchanged, and a rerun appends nothing to the log.
- [ ] The run report lists each cut group (pieces, cut answers, keys left alone by ties, pieces still held back with reason), and the report and CLI summary show counts.

### Round-2 set and freeze (test-first through the tune, label and score CLIs)
- [ ] The known sets are `tuning`, `scoring` and `scoring2`, each with its own seed.
- [ ] `scoring` checks `frozen.json`; `scoring2` checks `frozen-2.json`. `--freeze` can write `frozen-2.json` with the existing guards (dirty tree, stale tuning report, write-once, model pins).
- [ ] The `scoring2` draw excludes every tuning and round-1 scoring pair, refuses if either draw is missing, and uses the same strata and sizes (60 / 25 / 15).
- [ ] Scoring `scoring2` refuses a run whose config differs from its draw's.

### Tuning check
- [ ] A command compares two alias maps (run 2 and run 3) against the tuning labels. It lists pairs that merge only in the newer map and agreement with the labels, and exits non-zero if a newly merged pair is labelled not same.

### Review and verification
- [ ] `mattpocock-skills:code-review` run; every finding fixed or explained.
- [ ] `uv run ruff check .` and `uv run --env-file .env pytest` pass.

### Paid run (commands with `env -u ANTHROPIC_BASE_URL`; estimate shown first; $1.91 left of the cap)
- [ ] Run 3 (`resolve.run`) replays cached adjudicator answers with no new calls; its cost is reported.
- [ ] Tuning check run 2 vs run 3. **Stop and report to the user** if any newly merged tuning pair is labelled not same.
- [ ] Freeze `frozen-2.json` on a clean tree.
- [ ] Draw, label (about $0.06) and score `scoring2` once.
- [ ] README: two rows, round 1 and round 2, each with commit, config hash and intervals, marked as different draws, not a paired comparison, and "agent-labelled (gpt-6-luna)". Round 2 is the current figure.
- [ ] BUILD-LOG entry with round 2 results, regressions, costs and a "Next session starts with" note. OPEN-DECISIONS updated with what stays open.
