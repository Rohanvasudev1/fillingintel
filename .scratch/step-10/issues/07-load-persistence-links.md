# 07: Load the persistence links

**What to build:** A full rebuild includes PERSISTS_AS edges from the committed link file, and the README shows the load report. Spec: `.scratch/step-10/spec.md` (Loader command).

**Blocked by:** 04, 05

**Status:** ready-for-agent

- [ ] The builder accepts the link file; each link maps both risk factor keys to nodes and becomes one PERSISTS_AS edge with its two evidence entries. A link naming a missing risk factor fails the build with its keys.
- [ ] The load report records the link file's hash and PERSISTS_AS counts by method.
- [ ] A full rebuild of the local Neo4j passes `validate_graph()` with zero violations; the load report is committed.
- [ ] README gets a short table of the load report: counts per label and edge type, conflicts, skipped items, the chunk gap.
- [ ] Test-first; `mattpocock-skills:code-review`; ruff and pytest green.
