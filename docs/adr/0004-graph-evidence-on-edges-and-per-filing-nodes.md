# Graph evidence lives on the edge, and risk factors and metric values are nodes per filing

A Neo4j relationship cannot point at a node, so GRAPH-LAYER.md's `(:*)-[:EVIDENCED_BY]->(Chunk)` works for nodes but not for edges. Every extracted edge therefore stores its evidence itself, as two parallel list properties: `chunk_ids` (non-empty) and `evidence_spans` (one quote per chunk). Every extracted node keeps `EVIDENCED_BY` edges to Chunk nodes, which the local search arm walks to collect chunks. Neo4j Community cannot enforce property existence, type or key constraints (docs/research/neo4j-driver-and-constraints.md), so the guarded write path and `validate_graph()` enforce this rule, not the database. An edge is identified by (start key, type, end key), plus `role` where the type has one. Writing it again adds chunks to its lists, never a second parallel edge.

Most extracted labels (Organization, Segment, Product, Regulation, LegalProceeding, Agreement, Facility, Person) are one node across all filings, with time read from the evidence chunks' filings. RiskFactor is one node per filing, linked across years by `PERSISTS_AS`. MetricValue is one node per (filing, subject, metric, period), so a restated figure is a second node from the later filing rather than an overwrite.

Decided on 2026-10-07 in the Step 7 grilling.

## Considered options
- **Reification: a Fact node between the two ends of each edge, with `EVIDENCED_BY` to its chunks.** Evidence then looks the same for nodes and edges, but every traversal doubles in length, which makes text-to-Cypher in Step 11 harder to generate and to check.
- **A `chunk_ids` list on nodes too, with no `EVIDENCED_BY` edges.** Simpler to validate, but the local search arm would lose its one-hop path from an entity to its chunks.
- **One node per filing for every label.** Uniform, but "TSMC" would become 24 nodes that entity resolution would have to merge back.

## Consequences
- "Which edges came from chunk X?" cannot use an index: no Neo4j index matches one element of a list property. It scans every edge of that type, which is cheap at 24 filings and would need revisiting at a much larger corpus.
- Deleting or re-chunking a filing means removing its chunk IDs from edge lists, and deleting edges whose lists become empty. Step 10 owns this.
- Questions about change between filings (risk factors, figures) compare per-filing nodes. Other edges answer them only through the filings of their evidence chunks.
