# 06: Persistence scoring

**What to build:** The persistence links get one score: precision and recall on 100 pairs labelled by gpt-6-luna, in the README as agent-labelled. Spec: `.scratch/step-10/spec.md` (Persistence scoring).

**Blocked by:** 05

**Status:** ready-for-agent

- [ ] A freeze file pins the persistence config hash, `persist/v1`, the label prompt and both models with effort, before the draw; it refuses a dirty tree.
- [ ] A draw of 100 pairs from the linked filing pairs, 60 adjudicated / 25 near / 15 rest, its own fixed seed, committed write-once.
- [ ] A label prompt for gpt-6-luna that never sees the adjudicator's answer, labels committed write-once (about $0.10, estimate first, OPENAI_BASE_URL refused).
- [ ] Precision and recall on the selected links, weighted by stratum, with fixed-seed bootstrap 95% intervals, scored once; the score file is committed.
- [ ] README row with commit and config hash, labelled "agent-labelled (gpt-6-luna)". A poor score is reported, not tuned.
- [ ] Reuses Step 9's draw, label and score code where it fits; test-first for new logic; `mattpocock-skills:code-review`; ruff and pytest green.
