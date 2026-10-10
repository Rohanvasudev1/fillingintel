# 04: Labeller, sampling and scoring

**What to build:** the measurement side of entity resolution. A labeller on gpt-6-luna labels pairs it draws from the tuning set and, once the rules are frozen, from the stratified scoring set. `python -m resolve.score` scores the alias map against the labels: pairwise precision and recall on the final groups, weighted by stratum, with bootstrap intervals, plus the adjudicator's accuracy and the share of true matches the candidate rules found. Every figure is labelled "agent-labelled (gpt-6-luna)" (spec: Implementation Decisions, label, sample and score; user stories 61–74, 76–78, 81).

**Blocked by:** 02. Runs in parallel with 03 and 05.

**Parallel-safety:** touches the label, sample and score modules in `resolve/`, `prompts/label/`, and only the labels-and-scoring section of the run report that 02 set up. Reuses the eval harness's bootstrap and the judge's OpenAI path without changing them.

**Status:** ready-for-agent

- [ ] `prompts/label/v1.md`, pinned by hash, with the same-entity rule from CONTEXT.md. The request carries names, label, evidence spans and chunk text, and a test checks it carries no resolver output (no decision, rule or score).
- [ ] gpt-6-luna at effort `high` through the existing OpenAI Responses path: strict json_schema, store=false, no sampling settings, response cache. Reply: `same` (bool), a quote and chunk ID per node, a reason.
- [ ] `resolve.label --set tuning|scoring` checks `OPENAI_API_KEY`, refuses when `OPENAI_BASE_URL` is set, shows an estimate and refuses over `--max-cost`. Labels go to committed write-once files under `benchmarks/resolution/`; relabelling an existing pair is refused.
- [ ] Tuning draw: about 100 same-label pairs from all pairs, fixed seed.
- [ ] Scoring draw: 100 pairs not in the tuning set, stratified 60 candidate pairs / 25 just below the cutoff / 15 the rest, fixed seed. It refuses to run unless the config and prompt hashes are marked frozen, and records them.
- [ ] `score(labels, alias_map, strata)`: a labelled pair is predicted-same when both keys share a canonical key. Weighted precision and recall, fixed-seed bootstrap 95% intervals, adjudicator accuracy on the pairs it judged, and the rules' share of true matches. Raw counts per stratum. Tests against hand-computed values: three strata, a merge through a chain, an empty stratum, a known interval.
- [ ] Report header: commit, labeller and adjudicator models and efforts, prompt versions and hashes, config hash, ontology version, and the label "agent-labelled (gpt-6-luna)".
- [ ] Recorded real labeller replies for a few run 1 pairs, captured once after asking the user and replayed offline.
- [ ] Exit codes 0, 2, 3, 4 as the other commands.
- [ ] Rule tests written first and shown failing (`/tdd`). Code review with `mattpocock-skills:code-review`. Ruff and pytest pass, CI is green. BUILD-LOG entry appended.
