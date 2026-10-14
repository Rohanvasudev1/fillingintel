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

## Extension, 2026-10-09 (Step 8, ontology version 2)
Each piece of evidence also records the extractor's confidence (`stated`, `implied` or `uncertain`) and the extract prompt version that produced it. Extracted edges gain two more parallel lists, `confidences` and `extract_prompts`, one entry per chunk in `chunk_ids`; `EVIDENCED_BY` gains `confidence` and `extract_prompt`. Nodes carry neither. The write path and `validate_graph()` check the four lists' lengths and the confidence values. The decision above is unchanged.

## Amendment, 2026-10-14 (Step 10)
Two things above no longer hold as written. Writing an edge again does not add chunks to its lists, and Step 10 does not remove chunk IDs from a live graph:
- **Evidence is combined before the write.** The graph builder (`graph/builder.py`) gathers every entry for a node or edge across filings and merged keys, keeps one entry per chunk (the most confident: `stated` > `implied` > `uncertain`, ties to the later filing), orders entries by filing date and then chunk, and settles a single-valued property by the same rule, counting each disagreement. `write_batch()` writes the four lists and each `EVIDENCED_BY` entry as given, and `check_batch()` rejects a batch that holds the same node key or edge identity twice.
- **The graph is rebuilt, never appended to.** `python -m graph.load --rebuild` deletes every node but `:GraphMeta` and writes the whole batch in one transaction, so a failed or rejected write keeps the previous graph. Deleting or re-chunking a filing means rebuilding. Incremental loads are an open question for a larger corpus (OPEN-DECISIONS).
The evidence model itself (lists on edges, `EVIDENCED_BY` on nodes, per-filing RiskFactor and MetricValue) is unchanged.

## Amendment, 2026-10-14 (Step 11 grilling, ontology version 4)
A risk factor's evidence now covers its body, not only its heading. Step 10's multi-hop check (q0127) found the graph linked NVIDIA's demand risk factor across years but could not reach the list of causes, which sat in the chunk after the heading. A deterministic builder step adds an `EVIDENCED_BY` edge from each RiskFactor to every chunk its body touches. The body starts after the heading's last appearance in the filing's risk-factor section, which skips the summary list, and ends at the next RiskFactor heading or the end of the section. Each body edge has the first 200 characters of the body text in that chunk as `evidence_span`, `confidence` `stated` and `extract_prompt` `body/v1`, so it is told apart from extracted evidence. Bodies longer than 5 chunks are listed in the load report. Ontology version 4 also allows Company as a `CONCERNS` end. The evidence model is unchanged.
