# 01: Ontology version 3 and extract prompt v2

**What to build:** everything extraction needs before the Step 9 runs. The graph ontology moves to version 3 (ADR-0005): a MarketPlatform label, Segment meaning a reportable segment only, Segment and MarketPlatform keys that include the filer's CIK, and an `aliases` list on cross-filing extracted nodes. A new extract prompt `extract/v2` teaches the segment-versus-market-platform distinction. `extract.run` accepts 10-Qs and shows the running extraction total against the $40 cap. `extract.review` shows the review standard before round 3 (spec: Implementation Decisions, Ontology version 3 and Extraction; user stories 1–23).

**Blocked by:** None (can start immediately). Runs in parallel with 02.

**Parallel-safety:** touches `graph/`, `extract/`, `prompts/extract/` and their tests only. Does not touch `resolve/`, `ingest/voyage.py` or `pyproject.toml`.

**Status:** ready-for-agent

- [ ] `ONTOLOGY_VERSION` is 3 and the schema hash changes. MarketPlatform is an extracted label with its one-line description; Segment's description says "reportable segment, as the filer's segment note defines it".
- [ ] `HAS_MARKET_PLATFORM` Company→MarketPlatform exists. MarketPlatform is an allowed end of `MEASURES` and `AFFECTS` and an allowed start of `OFFERS`. Any other endpoint pair is still rejected.
- [ ] Cross-filing extracted labels accept an optional `aliases` list of strings; `check_batch()` and `validate_graph()` reject any other type.
- [ ] Segment and MarketPlatform keys are `{label}:{cik}:{norm(name)}`, built in code from the filing's CIK; other key formats are unchanged. A MetricValue measuring a Segment or MarketPlatform carries that subject key.
- [ ] Broken cases, one rule each: a MarketPlatform as the end of `HAS_SEGMENT`; a Segment key without a CIK; `aliases` not a list of strings; `AFFECTS` to an unlisted label.
- [ ] `prompts/extract/v2.md` added; v1 kept unchanged. v2 explains reportable segment versus market platform with NVIDIA's split as the example. Its version string is `extract/v2@<hash8>`, and the output schema offers MarketPlatform and `HAS_MARKET_PLATFORM`.
- [ ] Recorded real replies (captured once after asking the user) for: NVIDIA's segment note naming both segments and platforms, and one chunk from a fixture 10-Q. Replayed offline, they yield Segment and MarketPlatform nodes with CIK keys and pass `check_batch()`.
- [ ] `extract.run` runs on a 10-Q accession; help text and docstrings no longer say "10-K". Before each run it shows the total cost so far (summed from the committed run reports, or a small ledger; pick the simpler) and refuses to push the total past $40 without an explicit flag. Exit code 3 on refusal.
- [ ] `extract.review` prints the review standard before the first triple: a span naming one end is correct if the same chunk names the other; a shortened role is correct if it is the start of the full title and does not mislead. The under-15-second prompt also applies to miss-check answers. Tested with injected `ask`, `out` and fake clock.
- [ ] The local Neo4j's v1 `:GraphMeta` is not touched; the BUILD-LOG entry reminds the user it must be cleared before Step 10.
- [ ] Rule tests written first and shown failing (`/tdd`). Code review with `mattpocock-skills:code-review`. `uv run ruff check .` and `uv run --env-file .env pytest` pass, and CI is green. BUILD-LOG entry appended.
