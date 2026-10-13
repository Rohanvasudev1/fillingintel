# 08: Multi-hop check and closing Step 10

**What to build:** The RUNBOOK stop condition: three agent-drafted dev multi-hop questions answered with hand-written Cypher against the loaded graph, and the Step 10 records closed. Spec: `.scratch/step-10/spec.md` (Multi-hop check, Decisions recorded, OPEN-DECISIONS).

**Blocked by:** 06, 07

**Status:** ready-for-agent

- [ ] Three Cypher files committed: TSMC reliance (NVIDIA, AMD, Intel), competitors naming each other, NVIDIA's customer-investment risk factor across the FY2025 and FY2026 10-Ks (PERSISTS_AS).
- [ ] A small runner executes them in read transactions and applies the pass rule: for each company or period the question asks about, the returned evidence chunk IDs include at least one of the record's gold chunks.
- [ ] Each query, its output and verdict go into BUILD-LOG, labelled agent-drafted. A failure is reported as a reason to revisit the ontology; the question and pass rule are not changed.
- [ ] ADR-0004 gets a dated amendment (evidence combined before the write by the most-confident rule; the graph is rebuilt, never appended to).
- [ ] CLAUDE.md Decisions gets the Step 10 entry; OPEN-DECISIONS drops the settled Step 10 items and keeps ER recall, `MAX_CHUNKS_PER_NODE`, re-extracting Intel 0017, and adds incremental loads.
- [ ] RUNBOOK Step 10 outcome written; BUILD-LOG entry ends with "Next session starts with".
- [ ] `mattpocock-skills:code-review`; ruff and pytest green.
