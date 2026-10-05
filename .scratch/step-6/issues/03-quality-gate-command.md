# 03: Quality gate command, run locally

**What to build:** `python -m eval.gate` runs the quality gate against a database loaded from the snapshot (ticket 02). It opens the vector arm with the snapshot query embedder, runs the retrieval-only method (ticket 01) on the 82 answerable `dev` questions, and scores recall@5, recall@10 and the retrieval wrong-evidence rate (hard negatives ranked above a gold chunk; no answer, so no citations) for lookup, local, multi_hop, global and pooled. It compares them with the gate baseline: a lower recall or a higher wrong-evidence rate fails, after rounding to a fixed number of decimal places. `--update-baseline` rewrites the gate baseline. The first gate baseline is committed in this ticket. See the spec's Gate section and ADR-0003.

**Blocked by:** 01, 02

**Status:** ready-for-agent

- [ ] Exit codes: 0 pass; 2 bad input; 3 could not run; a distinct code for scores dropped.
- [ ] On a drop, output names each class and number that dropped, with baseline, current and delta values.
- [ ] Output is labelled "agent-drafted questions" and printed as a per-class table; when `GITHUB_STEP_SUMMARY` is set, the same table is appended to that file, and only then.
- [ ] The gate baseline file records the gated numbers, n per class, commit, snapshot SHA-256, question set hash, embedding model and k. A mismatch in any of the last four is bad input, not a pass.
- [ ] `--update-baseline` writes the file atomically and prints the changes from the old file.
- [ ] The first gate baseline is created and committed. A test checks it against the committed run `2026-10-05-vector-dev-96e8c69.json`: recall per class exactly, and wrong evidence against that run's per-question hits whose `above_gold` is true.
- [ ] Gate tests (written test-first, each shown failing first): passes against its own baseline; fails naming the class when the baseline is raised by one question's worth; fails with k = 1; bad input on each hash, model or k mismatch; identical numbers on two runs; `--update-baseline` then a passing run.
- [ ] The gate never constructs an answer model or judge and never reads an API key; a test runs it with the network blocked.
- [ ] The gate has no way to select the `test` split.
- [ ] Agents never lower the gate baseline to get a change through; any lowering needs the user's yes in chat and a BUILD-LOG line (invariant 5).
- [ ] `uv run ruff check .` and `uv run --env-file .env pytest` pass, and `python -m eval.gate` passes locally.
