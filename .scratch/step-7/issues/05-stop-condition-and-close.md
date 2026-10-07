# 05: Stop condition and close Step 7

**What to build:** evidence that the RUNBOOK stop condition holds outside the tests, and the step's records closed. Against the user's local `docker compose` Neo4j (the real graph instance, which is empty at this point), run `apply_constraints` and `python -m graph.validate` on the empty graph. Against the `neo4j-test` instance, load the valid test graph and validate it, then break one thing and show the command exiting 4. Record the output in BUILD-LOG and add a "Step 7 — outcome" section to the RUNBOOK, as Steps 3 and 6 have.

**Blocked by:** 04 (validate_graph() and `python -m graph.validate`)

**Status:** ready-for-agent

- [ ] The local Neo4j shows the constraints in `SHOW CONSTRAINTS` and the `:GraphMeta` node; `python -m graph.validate` exits 0 on it. Commands and output go in BUILD-LOG with the commit.
- [ ] The broken-graph demonstration on `neo4j-test` exits 4 with the violation shown, and the instance is wiped afterwards.
- [ ] RUNBOOK gains "Step 7 — outcome" naming the PRs and CI runs.
- [ ] Ticket statuses 01–05 and the spec's status updated; OPEN-DECISIONS and CLAUDE.md Decisions checked for anything the tickets settled or raised.
- [ ] BUILD-LOG entry dated, ending with "Next session starts with" (Step 8 via `/grill-with-docs`).
- [ ] `uv run ruff check .` passes.
