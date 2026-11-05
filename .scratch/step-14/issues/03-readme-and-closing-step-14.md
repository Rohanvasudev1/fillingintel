# 03: README and closing Step 14

**What to build:** a reader opening the repo sees what FilingIntel does with a screenshot, the `test` benchmark table up top, an honest interpretation of where graph lost and what it cost, a short architecture section with one diagram, the numbers most projects don't publish, and what the author would do differently. Every number in the README is generated from committed files and checked in CI. The docs describe what was built, the repo has a licence, and Step 14's stop condition is met. Spec: `.scratch/step-14/spec.md` (README generated blocks; README prose; Docs and licence). Vocabulary: CONTEXT.md.

**Blocked by:** 02 (The UI), for the screenshot and the "what it does" part

**Status:** ready-for-agent

**Parallel sessions:** as ticket 01. No paid calls.

- [ ] `eval.results_table` writes two README blocks between marker comments and leaves the prose around them untouched; `--check` covers `results.md` and both blocks (exit 4 on a difference), in the existing CI job. Tests as for `results.md`: built from fixture inputs, stable under two hash seeds, prose outside the markers unchanged.
- [ ] Block 1, the `test` headline table: the four arms and both routers; recall@10 per answerable class with n, pooled recall@10 with 95% interval, pooled paired difference against `vector` with interval, pooled faithfulness (uncalibrated), mean cost per question, p95 total latency; a label line (split, 47 agent-drafted questions, judged uncalibrated, classes under 10 directional, commit) and one line for decline and unanswerable.
- [ ] Block 2, numbers most projects don't publish: ER precision and recall (round 2), Cypher validity first pass and after repair, router `test` accuracy with Wilson interval, extraction violation and rejection rates with both review rounds, persistence precision and recall, `graph_global` provenance breaks, corruption-test catch rates; each with label, commit and a link to its `results.md` section.
- [ ] README in order: what it does (three sentences, the screenshot); block 1; interpretation; architecture (about 8 lines, link to the diagram); block 2; what I'd do differently; Running it (compose, env, load, app command); Scope. The per-step sections removed. The stack lists no LangGraph, DeepEval or FastAPI.
- [ ] Interpretation and "what I'd do differently" drafted from BUILD-LOG (the `test` entry and "Findings worth telling"), every number traced to a committed file; marked in the PR for the user's edit. Judged numbers say "uncalibrated".
- [ ] ARCHITECTURE.md: one Mermaid flowchart of what was built replaces the ASCII art; drift fixed (agent-drafted eval set, no path precision, no Cypher floor in CI, no FastAPI). RUNBOOK repo tree drops `api/`; `api/` deleted.
- [ ] An MIT LICENSE, and a README line that filings are SEC public records and the questions are agent-drafted.
- [ ] OPEN-DECISIONS updated (calibration still deferred; Step 13b still skipped); RUNBOOK Step 14 "As built" note.
- [ ] Stop condition checked and reported: app answers a sample through all six options under the cache-only check; AppTest green in CI; `--check` covers the README; screenshot committed; every README number traces to a committed file. The user's approval of the prose happens in PR review.
- [ ] `uv run ruff check .` and the full `uv run --env-file .env pytest` pass; `mattpocock-skills:code-review` findings fixed; BUILD-LOG entry closing Step 14; branch, push and PR.
