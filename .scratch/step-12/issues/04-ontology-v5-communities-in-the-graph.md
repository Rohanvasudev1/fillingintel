# 04: Ontology version 5, communities in the graph

**What to build:** After `graph.load --rebuild`, the graph holds every community as a derived `:Community` node with its report, membership and parent, written in the rebuild's single transaction, and `graph.validate` checks them. Spec: `.scratch/step-12/spec.md` (Ontology version 5; Loading; Validation). Decision: ADR-0004 amendment of 2026-10-15. Vocabulary: CONTEXT.md (derived node, community).

**Blocked by:** 03

**Status:** ready-for-agent

**Parallel sessions:** one ticket at a time from here. `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket. Step 12 spend is capped at $6 in all, counting committed spend; show the estimate before any paid call.

- [ ] `ONTOLOGY_VERSION` 5: `:Community` (`community_id` key with a uniqueness constraint, `level`, `member_hash`, `title`, `report`, `report_chunk_ids`, `report_prompt`, `ontology_version`); `IN_COMMUNITY` from an extracted non-MetricValue node to a lower Community; `PARENT_OF` from a top to a lower Community. Derived edges carry `ontology_version` only. Schema text and hash regenerate.
- [ ] `graph.load --rebuild` reads the latest communities and reports files, matches reports by `member_hash`, and writes communities in the same transaction. It refuses (exit 2, nothing written) when a reportable community has no matching report or the communities file was made from a different community graph input. Unreportable communities load with an empty report.
- [ ] It upgrades a version-4 `:GraphMeta` and refuses a newer one. The load report gains community counts.
- [ ] `validate_graph()` adds: members are extracted non-MetricValue nodes; one parent per lower Community; every top Community has a child; every `report_chunk_ids` entry is a member's evidence chunk (top: a child's member); the recomputed member hash matches.
- [ ] Test-first on `neo4j-test`, one break per case: each validator rule, the missing and stale report refusals, the upgrade and the newer-version refusal.
- [ ] Load run 5 on the local graph: zero violations with the Postgres check; report committed. `graph.multihop` still passes 3 of 3.
- [ ] `mattpocock-skills:code-review` (Cypher: injection safety, constraints); ruff and pytest green; BUILD-LOG entry.
