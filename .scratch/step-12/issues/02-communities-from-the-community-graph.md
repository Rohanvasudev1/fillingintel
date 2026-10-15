# 02: Communities from the community graph

**What to build:** `python -m graph.communities` builds the community graph from the same builder output `graph.load` writes, runs Leiden at exactly two levels, and writes a committed communities run file that is identical on every run. Offline, $0. Spec: `.scratch/step-12/spec.md` (Community graph; Communities; `python -m graph.communities`). Research: docs/research/communities-and-global-search.md (sections 1 and 4). Vocabulary: CONTEXT.md (community graph, co-occurrence edge, community).

**Blocked by:** None (can start immediately)

**Status:** done

**Parallel sessions:** tickets 01 and 02 may run at the same time in their own worktrees. `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest, and iterate with `NEO4J_TEST_URI` unset when your tests do not need Neo4j. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket. Step 12 spend is capped at $6 in all; show the estimate before any paid call.

- [x] `graspologic-native==1.3.1` added as a direct dependency, pinned exactly (approved 2026-10-15); a test imports it with the network blocked.
- [x] A pure function turns builder output into the community graph: extracted nodes other than MetricValue; extracted edges between them weighted by their evidence chunk count; co-occurrence edges weighted by shared evidence chunks; one undirected weight per pair; no Company, Filing, Period or Chunk nodes; no self-loops; nodes with no edge returned separately.
- [x] A pure `communities` module: Leiden (modularity, resolution 1.0, randomness 0.001, one iteration, fixed seed) over the whole graph gives the top level; the same call inside each top community over 25 members gives the lower level; a smaller top community is its own lower community. Each community has a deterministic `community_id`, a `member_hash` (SHA-256 over sorted member keys), its level and its parent. Communities under 3 members are marked not reportable.
- [x] Settings live in one frozen config whose hash is recorded. They are fixed now and never tuned on eval questions.
- [x] Test-first on a small graph around real fixture chunk IDs: exclusions, both weights, determinism with the seed, the 25-member split rule, member hashes stable under input order, singletons reported. One test runs over the full builder output from the fixtures.
- [x] `python -m graph.communities` writes `benchmarks/communities/communities-run{N}.json` with commit, settings and config hash, library version, seed, a hash of the community graph's input, counts and size distribution per level, singletons left out, unreportable communities and every community's members. Exit codes as other graph CLIs.
- [x] Run 1 on the local data is committed, and a second run gives an identical file apart from run metadata. Expected near the grilling measurement (34 top, 111 lower on load run 4); report any large difference.
- [x] `mattpocock-skills:code-review`; ruff and pytest green; BUILD-LOG entry; CLAUDE.md Commands gains the CLI.
