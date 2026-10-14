# 01: Risk factor body evidence and ontology version 4

**What to build:** Every risk factor in the graph is evidenced by every chunk of its body, not only its heading chunk, and the graph is on ontology version 4 (Company allowed as a `CONCERNS` end). After a rebuild, Step 10's multi-hop check passes q0127. Spec: `.scratch/step-11/spec.md` (Body evidence and ontology version 4). Decisions: ADR-0004 amendment of 2026-10-14 (Step 11). Vocabulary: CONTEXT.md (risk factor body, evidence).

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Parallel sessions:** other Step 11 tickets may run at the same time in their own worktrees. `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest, and iterate with `NEO4J_TEST_URI` unset when your tests do not need Neo4j. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket.

- [ ] The pure graph builder adds an `EVIDENCED_BY` entry from each risk factor to every chunk its body touches. The body starts after the heading span's last appearance in the risk-factor section (Item 1A for a 10-K, Part II Item 1A for a 10-Q) and ends at the next risk factor heading found the same way, or at the section end.
- [ ] Body entries carry `extract_prompt` `body/v1`, `confidence` `stated`, and `evidence_span` set to the first 200 characters of the body text inside that chunk, verbatim. Overlap with extracted entries is settled by the existing combining rule.
- [ ] A risk factor whose heading can't be located gets no body entries and is counted in the build report. Bodies over 5 chunks are listed. There is no cap.
- [ ] The builder's prompt-mix check still covers extract files only.
- [ ] `ONTOLOGY_VERSION` 4: `CONCERNS` allows Company as an end. The 5 previously dropped agreement edges load.
- [ ] `graph.load --rebuild` upgrades a `:GraphMeta` with a lower version in the rebuild transaction (re-applying constraints) and records it in the load report. A higher or unparseable version is refused with nothing changed.
- [ ] The load report notes that the evidence was extracted under ontology 3's `extract/v2@34a82e2b` prompt. No re-extraction.
- [ ] Test-first on the real gzipped fixture filings: a body ending at the next heading, one ending at the section end, the summary list skipped, an unlocatable heading, a long body flagged. On `neo4j-test`: the upgrade is applied and a newer version is refused.
- [ ] Load run 4 on the local graph: `graph.validate` reports zero violations, with the Postgres check. The report and its counts (body entries, flagged bodies, unlocated headings) are committed.
- [ ] `graph.multihop` rerun with the questions and pass rule unchanged, and the run file committed. **If q0127 still fails, stop and report to the user before closing the ticket.**
- [ ] `mattpocock-skills:code-review`; ruff and pytest green; BUILD-LOG entry.
