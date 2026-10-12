# Step 9b: Cut held-back resolution groups into pieces

Status: ready-for-agent
Sources: RUNBOOK Step 9b, docs/OPEN-DECISIONS.md (Entity-resolution recall), the Step 9 ticket 06 BUILD-LOG entry (2026-10-12), ADR-0006, and the grilling session of 2026-10-12, whose decisions are in CLAUDE.md Decisions (Step 9b entry). External research: docs/research/er-cluster-splitting.md (how ER libraries and the literature handle clusters with cannot-link pairs; multicut and correlation clustering; measurements on run 2's held-back groups). Vocabulary: CONTEXT.md (held-back group, group cut, piece).

## Problem Statement

Step 9's resolver merges nodes that the adjudicator judged `same`, grouped by union-find. A safety rule holds back a whole group when it has more than 5 names or when two of its keys were judged `related` or `distinct`. Run 2 held back 21 groups this way, and nothing in them merged.

That rule cost most of the recall. Scoring round 1 (agent-labelled, gpt-6-luna) gave precision 1.000 and recall 0.387 [0.199, 0.611]. Five of the nine missed candidate-stratum matches were `same` answers inside held-back groups: AMD Versal, the MI308 products, the US export-licensing rules. Nine of the 21 groups were held back for size alone and look like correct merges: ZT Systems, GlobalFoundries, MI308, the Altera transaction agreement, the CHIPS Act. The other 12 hold a `related` or `distinct` pair. Most of those 12 hold a brand name ("Ryzen", "AMD Instinct", "EPYC") that a `same` answer chains to its own product line, which the adjudicator kept apart from the brand.

The graph loaded in Step 10 reads the alias map. Every true match left unmerged is a multi-hop question the graph cannot answer. A wrong merge, though, is worse than a miss, so the fix has to keep precision.

## Solution

A held-back group is no longer dropped. The resolver cuts it into pieces by removing the fewest `same` answers that leave no `related` or `distinct` pair inside one piece. Each piece within the name limit then merges. Deterministic merges (normalized names, the filer table) are never cut. When several smallest cuts tie, keys they place alike still merge, and a key they place differently stays unmerged. A group whose contradiction no cut can separate stays held back. `unsure` stays neutral. The name limit rises from 5 to 12 for every label, applies to each piece, and also bounds the exact search.

The change goes into the frozen config, so the config hash changes. Run 3 replays the cached adjudicator answers with no new calls. It is checked on the tuning set first: if any newly merged tuning pair is labelled not same, the step stops for the user before freezing. Once the check passes, the settings are frozen in a new `frozen-2.json`, and a fresh 100-pair set `scoring2` is drawn, labelled once by gpt-6-luna (about $0.06) and scored once ("scoring round 2"). The README shows round 2 beside round 1.

Prototype result on run 2, recorded in the research note and not a commitment. The 12 contradiction groups have a unique smallest cut in 9 cases, and the 3 ties are small groups of 3–4 names. Typical pieces:

| Held-back group | Pieces |
|---|---|
| EPYC (8 names) | {AMD EPYC, EPYC} and {6 CPU / processor names} |
| Instinct (9 names) | {AMD Instinct, AMD Instinct™} and {7 GPU / accelerator names} |
| Ryzen (6 names) | {Ryzen} and {5 processor names} |
| 364-day credit facility (4 names) | {$5.0 billion, two spellings} and {$8.0 billion, the unqualified name} |

## User Stories

1. As the project owner, I want a held-back group cut into pieces instead of dropped, so that true matches the adjudicator found are merged.
2. As the project owner, I want the cut to remove the fewest `same` answers, so that as few of the adjudicator's matches as possible are discarded.
3. As the project owner, I want no `related` or `distinct` pair to end up in one piece, so that a family and its member, or two variants, never merge.
4. As the project owner, I want deterministic merges never cut, so that names equal after normalization, and filers matched through the filer table, stay together as in Step 9.
5. As the project owner, I want a group whose contradiction lies inside a deterministic merge to stay held back, so that the resolver never breaks a rule it cannot satisfy.
6. As the project owner, I want each piece connected by `same` answers or deterministic merges, so that a piece never joins keys nothing links.
7. As the project owner, I want keys that tied smallest cuts place differently to stay unmerged, so that an arbitrary choice never decides a merge.
8. As the project owner, I want keys that every tied cut places alike to still merge, so that one disputed key does not cost a whole group.
9. As the project owner, I want the cut found exactly, not greedily, so that the result does not depend on the order decisions are read.
10. As the project owner, I want `unsure` answers treated as neutral as in Step 9, so that round 2 measures the cut and nothing else.
11. As the project owner, I want a 12-name limit applied to each piece for every label, so that a runaway chain in a later run is still held back.
12. As the project owner, I want the limit to bound the search too, so that the exact cut always runs in seconds.
13. As the project owner, I want a group under the limit with no contradiction to merge as in Step 9, so that clean groups behave exactly as before.
14. As the project owner, I want a group held back only for size cut by the same rule, so that one rule covers both reasons.
15. As the project owner, I want each piece logged as an ordinary `llm` merge with rule `group_cut`, so that ADR-0006's log and replay() work unchanged.
16. As the project owner, I want a piece's merge event to cite only the `same` answers kept inside it, so that its evidence matches what it merged.
17. As the project owner, I want a piece's merge undone by an ordinary split event, so that the existing undo path covers it.
18. As the project owner, I want the run report to list every cut group, with its pieces, the `same` answers cut, the keys left unmerged by ties and the groups still held back with their reason, so that I can check each cut by eye.
19. As the project owner, I want counts of cut groups, pieces merged, keys left alone by ties and groups still held back in the run report and the CLI summary, so that I see the effect at a glance.
20. As the project owner, I want the canonical-key rule unchanged for a piece, so that a filer's Company key still wins and otherwise the key with most evidence chunks.
21. As the project owner, I want the cut rule and the limit in the resolve config, so that the config hash records them and a run under the old rule cannot pass as the new one.
22. As the project owner, I want run 3 to make no new adjudicator calls, so that the change costs nothing to run.
23. As the project owner, I want a rerun on unchanged inputs to append nothing to the merge log, so that reruns stay idempotent.
24. As the project owner, I want the change checked on the 100 tuning pairs before freezing, so that the scoring set is never used to tune.
25. As the project owner, I want the step to stop and come to me if any newly merged tuning pair is labelled not same, so that a precision loss is my call, not an agent's.
26. As the project owner, I want the tuning check to report which tuning pairs now merge and their agreement with the labels, so that I see the gain as well as the risk.
27. As the project owner, I want the new freeze in `frozen-2.json` beside `frozen.json`, so that round 1's freeze stays as written.
28. As the project owner, I want the freeze to refuse a dirty tree, a stale tuning report or an existing file, as in Step 9, so that a freeze is a clean, one-time record.
29. As the project owner, I want a new set `scoring2` with its own seed, so that round 2 is a fresh draw.
30. As the project owner, I want `scoring2` to exclude every tuning pair and every round-1 scoring pair, so that no labelled pair seen before counts in round 2.
31. As the project owner, I want `scoring2` drawn 60 candidate / 25 near / 15 rest with the same strata definitions as round 1, so that the two rounds measure the same population the same way.
32. As the project owner, I want the `scoring2` draw to check `frozen-2.json` (config hash, label prompt, adjudicator prompt and model pins), so that round 2 is drawn only under frozen settings.
33. As the project owner, I want round 1's files left untouched and still checked against `frozen.json`, so that round 1 can be rescored as committed.
34. As the project owner, I want the `scoring2` labels, draw and score written once, like round 1's, so that a scored set cannot be redrawn after a look.
35. As the project owner, I want the cost estimate shown before labelling and the run kept under the Step 9 cap, so that round 2 stays within the $1.91 left.
36. As the project owner, I want round 2 scored once, with the same metrics and bootstrap as round 1, so that the rows are comparable.
37. As a reader of the README, I want round 1 and round 2 in two rows with commit, config hash and intervals, so that I see what changed and how sure each figure is.
38. As a reader of the README, I want a note that the rounds are different draws and not a paired comparison, so that I do not read the gap as an exact effect size.
39. As a reader of the README, I want every resolution number labelled "agent-labelled (gpt-6-luna)", so that I know no human checked the labels.
40. As a reader of the README, I want round 2 marked as the current figure, so that I know which one Step 10 relies on.
41. As the Step 10 implementer, I want the alias map rebuilt from the log after run 3, so that the graph loads the round-2 merges.
42. As the project owner, I want the BUILD-LOG entry to give round 2's precision and recall with commit and config, regressions and costs, so that the record stays complete.

## Implementation Decisions

- **Group cut** is part of grouping, which turns adjudicator decisions into merge groups and held-back groups. Its inputs are unchanged: the decisions, plus the deterministic merges as groups that cannot be cut. It still returns clean groups to merge and held-back groups. A clean group now also records whether it came from a cut. A cut group's record keeps the original group's keys, its pieces, the `same` answers cut and the keys left unmerged by ties.
- **The cut problem.** Within one union-find group, the cannot-link pairs are the decisions answered `related` or `distinct` between two of its keys. The cuttable links are the merging `same` decisions. The fixed links are the deterministic merges. A valid cut is a set of cuttable links whose removal leaves every cannot-link pair in different connected pieces. The smallest valid cut is found by exact enumeration in increasing size. If several valid cuts share the smallest size, a key goes into a piece only with the keys every tied cut places it with, and a key whose piece-mates differ between tied cuts stays alone. If no valid cut exists (a cannot-link pair joined by fixed links alone), the group stays held back with that reason.
- **Groups held back only for size** go through the same procedure. With no cannot-link pairs the smallest cut is empty, so the group becomes one piece, and that piece is held back only if it has more than the limit.
- **The limit.** A piece with more than 12 names stays held back. The limit is checked before the search too: a group with more than 12 names whose pieces cannot be found within the bound stays held back rather than running an unbounded search. The implementation sets the bound and documents it, keeping any group of 12 or fewer names fast.
- **Config.** The resolve config gains the name limit (12) and a named group rule (for example a "group cut" version string), both in the config hash. Old reports keep their old hash. Candidate rules and embeddings are unchanged.
- **Merge log.** Each piece is a merge event with method `llm` and rule `group_cut`. Its evidence quotes come only from the `same` decisions kept inside that piece. Merge IDs follow the existing hash of members, method and rule, so a rerun appends nothing. Earlier run-2 merges are untouched; no split events are needed, since held-back groups were never merged. ADR-0006 is unchanged.
- **Run report.** It adds a section on cut groups, with pieces, cut answers, keys left alone by ties, and pieces still held back with their reason, plus counts in the summary. The `held_back` list keeps its meaning (groups or pieces not merged) and gains the reason "no cut separates the contradiction" or "piece has N names, more than 12".
- **Tuning check.** After run 3 (`resolve.run`, cached, no new calls), the tuning set is scored with the existing scorer against run 3's alias map. Reported: the pairs whose merge status changed since run 2, and agreement with the labels. Any pair that merges now and is labelled not same stops the step before freezing. This is a report, not a tuning loop; nothing is adjusted on it.
- **Freeze.** The freeze writes `frozen-2.json` for round 2. Each scoring set is tied to its own freeze file: `scoring` to `frozen.json`, `scoring2` to `frozen-2.json`. The existing guards (dirty tree, stale tuning report, write-once, model pins) apply to both. Re-running the tuning comparison is not part of this step; the freeze takes the current config and pins.
- **Sets.** The known sets become `tuning`, `scoring` and `scoring2`, each with its own seed. Labelling, drawing and scoring accept `scoring2`. Its draw excludes the tuning draw's and the scoring draw's pairs and refuses if either is missing. It uses the same strata, sizes and near-stratum definitions, under the frozen-2 config. Write-once draw and label files follow the existing `{set}-draw.json` / `{set}-labels.jsonl` naming. The score report is numbered as before.
- **Scoring.** Unchanged metrics: weighted pairwise precision and recall on the final groups, fixed-seed bootstrap within strata, adjudicator accuracy, and the rules' share of true matches. The scorer reads the run report named by the scoring set's run config, which must match the draw's.
- **README.** The Entity resolution section gets a two-row table (round 1, round 2) with commit, config hash and intervals, the different-draws note, the "agent-labelled (gpt-6-luna)" label, and round 2 as the current figure. Round 1's text stays.
- **Costs.** Run 3 makes no adjudicator calls. Name embeddings are cached. Labelling `scoring2` costs about $0.06, within the $1.91 left of the ticket-06 cap. The estimate is shown before the paid call, and paid commands run with `env -u ANTHROPIC_BASE_URL`.

## Testing Decisions

- Good tests check what the resolver merges and holds back, given scripted answers, and what the CLIs write or refuse. They do not inspect how the cut is searched.
- **Seam 1: resolve_nodes() with the scripted fake adjudicator**, as in the existing grouping tests, using real-looking node names. Cases, each written first and shown failing:
  - a brand chained to its product line through one `same` answer, with two `related` answers, is cut into the brand piece and the product piece;
  - a group of 6–12 names with no contradiction merges as one piece (it was held back before);
  - a piece over 12 names stays held back, with its reason;
  - tied smallest cuts leave the disputed key unmerged and merge the keys all cuts agree on;
  - a contradiction inside a deterministic merge stays held back, with its reason;
  - `unsure` answers never cause a cut;
  - a piece's merge event cites only the `same` answers kept inside it, and its rule is `group_cut`;
  - the canonical key of a piece follows the existing rule, including a filer's Company key;
  - a rerun appends nothing to the log;
  - a clean group of 5 or fewer names merges exactly as before (regression);
  - the result does not change when the decisions are given in another order.
- **Seam 2: the CLIs**, as in the existing tune, label and score CLI tests with fakes and temporary directories:
  - the freeze writes `frozen-2.json` and refuses when it exists;
  - drawing `scoring2` checks `frozen-2.json`, excludes tuning and scoring pairs, and refuses when either draw is missing;
  - `scoring` still checks `frozen.json`;
  - scoring `scoring2` refuses a run config that differs from its draw's;
  - the config hash changes with the limit and the group rule.
- Run report shape: the existing run-report tests gain the cut-group section and counts.
- Prior art: the grouping tests, the pipeline tests, the log tests, the tune, label and score CLI tests, and the sample tests.
- Verification: `uv run ruff check .` and `uv run --env-file .env pytest` pass. The eval slice for this step is the tuning check and scoring round 2 itself.

## Out of Scope

- Any change to candidate rules, embeddings or the adjudicator prompt, or new adjudicator calls. The 4 `unsure` misses and the 3 near-stratum matches stay open in OPEN-DECISIONS.
- Treating `unsure` as a cannot-link, density or diameter checks, and per-label limits.
- A new merge-log event type, or any ADR-0006 change.
- Rescoring round 1's set under the new rule, or pooling the two rounds.
- Loading anything into Neo4j (Step 10).
- Human review of the labels.

## Further Notes

- The research note found that no ER library surveyed cuts clusters on cannot-link pairs; they all rely on numeric scores this resolver lacks. Splink's `ties_method="drop"` is the closest precedent for leaving tied keys unmerged. Exact enumeration took under 0.6 s on run 2's largest contradiction group.
- The prototype used a rough stand-in for the deterministic merges, so its pieces may differ from run 3's. Run 3's report is the record.
- Stop condition (RUNBOOK): round 2 precision and recall in the README beside round 1, with commit and config, before Step 10 loads the alias map.
