# Graph arms return ranked evidence chunks, and generated Cypher is guarded in code

`graph_local` and `graph_traversal` end the way `vector` does: a ranked list of at most 10 chunk IDs goes to the unchanged answer prompt (`answer/v1`), with the same citation rule and the same chunk-ID metrics. Arms therefore differ only in how they choose chunks, and every graph result can be compared with the control arm question by question. Generated Cypher must return a `chunk_ids` list column, as the Step 10 multi-hop queries do. The executor flattens the rows in order, drops duplicates and chunks outside the question filter, and cuts the list to 10.

Neo4j Community has no read-only users, and `EXPLAIN` alone is not a safe read-only test: `LOAD CSV` and `TERMINATE TRANSACTIONS` plan as `r` and run inside a READ transaction (docs/research/text-to-cypher-and-local-search.md). Unknown labels only raise warnings, and a reversed edge raises nothing. So every generated query passes five checks in order:
1. Text rules: one statement, and no `LOAD CSV`, `TERMINATE`, `SHOW`, `USE`, write clause or procedure `CALL`.
2. Our own check against the ontology, covering names and endpoint direction.
3. `EXPLAIN`, which must report `r` with no unknown-name notification.
4. Execution in a READ transaction with a driver timeout and a cap on rows read.
5. Server-wide timeout and memory limits in the compose file.

A failure gets one repair with the error message, and a second failure falls back to `graph_local`.

Decided on 2026-10-14 in the Step 11 grilling.

## Considered options
- **Graph paths in a new answer prompt, with path-level citations.** Closer to GRAPH-LAYER.md's answer prompt, but a new prompt would mix two changes into one comparison. Moved to Step 13 with path precision.
- **Free-form Cypher, with evidence mined from returned nodes and edges.** No contract for the model to meet, but it would rank chunks by guesswork. A fixed tail appended to the model's MATCH clauses breaks as soon as the model names its variables differently.
- **CyVer or LangChain's `CypherQueryCorrector` for validation.** CyVer is CC BY-SA and needs a live database. LangChain's corrector is regex-based and silently rewrites reversed paths.
- **Falling back to `vector` when a graph arm finds nothing.** That would mix the control arm into graph results. Routing between arms is the Step 13 router's job.

## Consequences
- A graph arm cannot win by giving the answer model structure that `vector` lacks. Path context is a separate, later comparison.
- Text rules can reject a valid read query, for example one using a procedure. The repair step and the fallback count shows how often this happens.
- Enforcing the row cap stops only reading on the client; the server stops at its timeout. Both Neo4j services carry the limits.
