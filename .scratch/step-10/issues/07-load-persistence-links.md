# 07: Load the persistence links

**What to build:** A full rebuild includes PERSISTS_AS edges from the committed link file, and the README shows the load report. Spec: `.scratch/step-10/spec.md` (Loader command).

**Blocked by:** 04, 05

**Status:** done

- [x] The builder accepts the link file; each link maps both risk factor keys to nodes and becomes one PERSISTS_AS edge with its two evidence entries. A link naming a missing risk factor fails the build with its keys.
- [x] The load report records the link file's hash and PERSISTS_AS counts by method.
- [x] A full rebuild of the local Neo4j passes `validate_graph()` with zero violations; the load report is committed.
- [x] README gets a short table of the load report: counts per label and edge type, conflicts, skipped items, the chunk gap.
- [x] Test-first; `mattpocock-skills:code-review`; ruff and pytest green.
