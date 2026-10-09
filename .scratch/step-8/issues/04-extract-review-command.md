# 04: `python -m extract.review`

**What to build:** the tool the user judges extraction with. It samples a review round of 30 validated edges from a run, shows each against its chunk with the evidence span highlighted, records correct, wrong or skip with a reason for every wrong, then shows 5 whole chunks with all their triples for a miss check, and writes a committed round file and summary (spec: user stories 46–57).

**Blocked by:** 03 (`python -m extract.run`)

**Status:** ready-for-agent

- [ ] Sample: 30 edges, random with a fixed seed, stratified by edge type in proportion to counts with at least one per type present; no edge judged in an earlier round of the same filing is drawn again. Skips don't count toward 30, and the sampler draws a replacement.
- [ ] Each triple shows start node, edge type, end node, properties, confidence and flags, then the chunk text with the span highlighted.
- [ ] Correct, wrong or skip; wrong needs a one-line reason, and an empty reason asks again. An answer in under 15 seconds asks "take another look?" once.
- [ ] Quit and resume continue the open round without losing or repeating a judgment.
- [ ] Miss check: 5 random chunks, each in full with all its extracted triples, answered "anything important missing? y/n" with a note for y.
- [ ] Round file in `benchmarks/extraction/`, one line per judgment with the triple, verdict, reason, reviewer, time taken, prompt version and commit. The summary gives correct of 30, accuracy with a Wilson 95% interval (tested at 26/30: 70% to 95%), accuracy per confidence level, the median time per answer and the miss rate labelled directional.
- [ ] Refuses non-interactive input, like `eval.review`. Agents never run the command; tests drive the review function with injected `ask`, `out` and a fake clock.
- [ ] Tests first and shown failing (`/tdd`). Code review with `mattpocock-skills:code-review`. `uv run ruff check .` and `uv run --env-file .env pytest` pass, and CI is green.
