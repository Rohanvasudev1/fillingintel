# Text-to-Cypher guards and GraphRAG local search (Step 11)

Research note for Step 11, written 2026-10-14. Step 11 adds graph retrieval arms over the Neo4j graph built in Step 10. This note covers three things: how to run LLM-generated Cypher safely on Neo4j Community 5.26, what Neo4j's own Text2Cypher work measured, and how Microsoft GraphRAG's local search and Neo4j's anchor-then-expand retrievers link a question to graph nodes and then to text chunks.

The driver, constraints, READ access mode, the `EXPLAIN` query type and driver timeouts are already in [neo4j-driver-and-constraints.md](neo4j-driver-and-constraints.md) (section 6.4–6.5). They are cited here, not repeated.

Sources: the Neo4j Cypher Manual 5 and Operations Manual 5 at neo4j.com/docs; the Python driver 6.3 API docs (https://neo4j.com/docs/api/python-driver/6.3/api.html); `neo4j/neo4j-graphrag-python` at tag `1.22.0` (2026-10-01); `langchain-ai/langchain-neo4j` at tag `libs/neo4j/v0.11.0` (2026-10-09); `microsoft/graphrag` at tag `v3.3.0` (2026-10-08); arXiv 2412.10064 and 2505.05118; the Hugging Face dataset cards and datasets-server API for `neo4j/text2cypher-2024v1` and `neo4j/text2cypher-2025v1`; Neo4j developer blog posts by the Text2Cypher authors; PyPI JSON metadata. Source files were read with `gh api` at the tags named above.

Claims marked "(ran)" were checked on 2026-10-14 against a throwaway `neo4j:5.26.31-community` container (`NEO4J_AUTH=none`, ports 17475/17688, removed afterwards; the project's containers were not touched), using `uv run --no-project --python 3.11 --with neo4j==6.3.1`. Anything I could not confirm is marked "unverified".

## Summary

- `EXPLAIN` plans without executing: after a dozen `EXPLAIN` writes the graph was unchanged (ran). `query_type` is `r` for reads, `rw` or `w` for writes, including the write procedures `db.createLabel`, `db.createProperty` and `db.createRelationshipType`, and `s` for `CREATE INDEX`, `dbms.*` procedures and `db.clearQueryCaches()` (ran). An "`r` only" rule therefore rejects all of these.
- Two gaps in that rule: `TERMINATE TRANSACTIONS` and `LOAD CSV` both report `r`. Both also run inside a READ transaction. `TERMINATE TRANSACTIONS` killed another session's transaction from `execute_read`, and `LOAD CSV FROM 'http://…'` tried to fetch the URL (ran). A text-level deny rule, or a server setting, is needed on top of `EXPLAIN` plus READ mode.
- APOC is not in the image: `SHOW PROCEDURES` lists no `apoc.*` (ran). neo4j-graphrag's `get_schema()` and LangChain's `Neo4jGraph` schema read both call `apoc.meta.data`, so neither works here. The project's generated schema text (`graph/schema_text`) is the available schema source.
- `db.memory.transaction.max` (default `0B`, meaning no limit) and `db.transaction.timeout` (default `0s`) are dynamic settings with no Enterprise label. `dbms.setConfigValue` does not exist on Community (ran), so they are set at startup through the environment. Over the memory limit the error is `Neo.TransientError.General.MemoryPoolOutOfMemoryError`. Because it is transient, `execute_query` retried it five times with backoff before giving up (ran).
- Row limits: wrapping as `CALL () { <q> } RETURN * LIMIT n` gives the same plan as an inner `LIMIT` (ran). It fails on unaliased return expressions, a trailing `;`, a `CYPHER` prefix, `SHOW …`, duplicate column names and a bare `CALL proc()` (ran). Fetching n records and then calling `consume()` or `rollback()` does not stop the server. The driver drains the rest, and in the test the query ran on until its timeout (ran).
- Unknown labels, relationship types and property keys give warnings, not errors: GQL statuses `01N50`, `01N51` and `01N52` (ran). A reversed edge direction gives no warning (ran). Syntax and scope errors raise `CypherSyntaxError` (ran).
- No maintained Python Cypher parser was found. CyVer 2.0.2 (CC BY-SA 4.0) validates against a live database. The libcypher-parser bindings were last released in 2017 and 2019.
- Text2Cypher (Neo4j, 2024): 44,387 question/schema/Cypher triples. Scores are Google-BLEU on the text and ExactMatch on sorted, stringified execution results. GPT-4o's execution ExactMatch was "about 30 percent". Pruning the schema to the terms in the question cut prompts about sixfold and helped an 8B model, while larger models lost about 2% on execution.
- GraphRAG local search embeds `"{title}:{description}"` per entity, takes the 2×k nearest entities (k = 10), then ranks their text units by entity order and by how many of the entity's relationships each unit supports. It fills a token budget: 50% text units, 15% community reports, the rest entities and relationships, in a 12,000-token default.
- Community 5.26 has full-text indexes (Lucene, 45 analyzers; a `LIST<STRING>` `aliases` property is indexed) and vector indexes (HNSW, `db.index.vector.queryNodes`, 1,024 dimensions accepted). The `SEARCH` clause is not in 5.26 (ran).

## A. Guards for running generated Cypher on Community 5.26

### A.1 `EXPLAIN` and `query_type`

- The driver documents `query_type` as `'r'` read-only, `'rw'` read/write, `'w'` write-only, `'s'` schema (API docs, `ResultSummary.query_type`, https://neo4j.com/docs/api/python-driver/6.3/api.html). It is `None` over the HTTP driver (same page, "Unavailable Features").
- `EXPLAIN` "will not run the query" according to the Cypher Manual's planning page (https://neo4j.com/docs/cypher-manual/5/planning-and-tuning/; wording not re-fetched for this note, so unverified here). In practice, after `EXPLAIN` of `CREATE`, `SET`, `DELETE`, `FOREACH … SET` and `CALL db.createLabel('Zed')`, the node count and `db.labels()` were unchanged (ran). Cost is one planning round trip. Plans are cached, so the real run that follows reuses the plan (plan cache behaviour unverified).
- What `EXPLAIN` reported on 5.26.31 (ran):

| Query | `query_type` |
|---|---|
| `MATCH (n) RETURN n`, `UNION` of two reads, `SHOW INDEXES`, `SHOW TRANSACTIONS`, `SHOW SETTINGS` | `r` |
| `CALL db.labels()`, `CALL db.index.fulltext.queryNodes(...)` | `r` |
| `TERMINATE TRANSACTIONS '…'` | `r` |
| `LOAD CSV FROM 'https://…' AS row RETURN row` | `r` |
| `CREATE (n:X) RETURN n`, `MATCH (n) CALL (n) { SET n.y = 2 } RETURN n` | `rw` |
| `SET`, `DELETE`, `FOREACH … SET`, `CALL db.createLabel/createProperty/createRelationshipType` | `w` |
| `CREATE INDEX`, `CALL dbms.components()`, `CALL dbms.listConfig()`, `CALL dbms.killConnections([...])`, `CALL tx.setMetaData({...})`, `CALL db.clearQueryCaches()` | `s` |

- Write procedures shipped in the image, from `SHOW PROCEDURES YIELD name, mode` with mode WRITE, SCHEMA or DBMS (ran): `db.createLabel`, `db.createProperty`, `db.createRelationshipType`, `db.create.setNodeVectorProperty`, `db.create.setRelationshipVectorProperty`, `db.create.setVectorProperty`, `db.index.vector.createNodeIndex`, `db.clearQueryCaches`, and `dbms.*` (`killConnection(s)`, `listConfig`, `upgrade` and others). `dbms.setConfigValue` is absent ("There is no procedure with the name `dbms.setConfigValue`", ran).
- APOC: zero `apoc.*` procedures (ran). The Step 7 note found that APOC loads only through `NEO4J_PLUGINS`, which the project leaves unset.
- `TERMINATE TRANSACTIONS`: inside `session(default_access_mode=READ_ACCESS).execute_read(...)` it terminated a running read transaction in another session. That session got `Neo.ClientError.Transaction.Terminated` (ran, with auth disabled; behaviour with a password-auth user unverified).
- `LOAD CSV` in READ mode: `LOAD CSV FROM 'http://127.0.0.1:9/x.csv'` failed only because nothing was listening: `Neo.ClientError.Statement.ExternalResourceFailed`, "Couldn't load the external resource" (ran). The server makes outbound requests from a read query. `dbms.security.allow_csv_import_from_file_urls` defaults to `true`, and `server.directories.import` limits `file:///` URLs to `/var/lib/neo4j/import` in the image (configuration settings page, https://neo4j.com/docs/operations-manual/5/configuration/configuration-settings/; values seen with `dbms.listConfig`, ran). I found no documented setting that blocks http(s) URLs for `LOAD CSV` on that page (unverified; an internal IP blocklist may exist but is not documented there).
- neo4j-graphrag already uses this guard. `Text2CypherRetriever.get_search_results` runs `EXPLAIN {query}` with `routing_=READ`, refuses any `query_type != "r"`, then runs the query with `routing_=READ` (`src/neo4j_graphrag/retrievers/text2cypher.py` at `1.22.0`). It has no `LOAD CSV` or `TERMINATE` check, and no timeout or row limit.

### A.2 READ access mode on one Community server

- Already established: writes in READ mode fail with `Neo.ClientError.Statement.AccessMode` on 5.26.31 Community, over `bolt://` and `neo4j://`, but the driver docs say not to rely on this for access control ([neo4j-driver-and-constraints.md](neo4j-driver-and-constraints.md) §6.4).
- New here: READ mode does not stop `TERMINATE TRANSACTIONS` or `LOAD CSV` (A.1, ran). It also allows `dbms.*` procedures in mode DBMS: `CALL dbms.listConnections()` ran under `routing_=READ` (ran).
- `server.databases.read_only` would block writes for the whole database (Step 7 note §6.4). It would not block the two statements above, since they are not database writes (inference, not run).

### A.3 Timeouts and memory

- Driver timeout (`Query(text, timeout=…)`, `unit_of_work(timeout=…)`) and server `db.transaction.timeout` are in the Step 7 note §6.5. New runs:
  - `Query(..., timeout=1.0)` failed after 2.95 s with `Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration`, `is_retryable() == False` (ran). Termination overruns the timeout, as the Step 7 note found.
  - With `NEO4J_db_transaction_timeout=30s` and no driver timeout, a long query failed after 30.1 s with the same code, `TransactionTimedOutClientConfiguration` (ran; I expected a server-configuration variant of the code).
- Memory settings, from `dbms.listConfig` on 5.26.31 (ran) and the configuration settings page:
  - `db.memory.transaction.max`: per transaction, default `0B` ("Zero means 'largest possible value'"), dynamic.
  - `db.memory.transaction.total.max`: per database, default `0B`, dynamic.
  - `dbms.memory.transaction.total.max`: all transactions, "Defaults to 70% of the heap size limit" (686 MiB in the default container), dynamic.
  - None carries an Enterprise label on the settings page. The memory guide says "When any of the limits are reached, the transaction is terminated without affecting the overall health of the database" (https://neo4j.com/docs/operations-manual/5/performance/memory-configuration/, "Limit transaction memory usage").
- With `NEO4J_db_memory_transaction_max=20m`, `UNWIND range(1, 5000000) AS x RETURN size(collect(x))` failed with `Neo.TransientError.General.MemoryPoolOutOfMemoryError`: "db.memory.transaction.max threshold reached". It is a `TransientError` with `is_retryable() == True`, so `execute_query` retried it five times with growing backoff (about 33 s in all) before raising (ran). An executor that does not want retries can catch it, use `session.run` (auto-commit, no retry), or lower `max_transaction_retry_time` (driver config; that last option is unverified).
- Since `dbms.setConfigValue` is absent on Community (ran), these dynamic settings are set at startup, in Docker as `NEO4J_db_memory_transaction_max` and `NEO4J_db_transaction_timeout` (ran). They then apply to every client, the loader included.

### A.4 Enforcing a row limit

- Wrapping (ran, 5.26.31; both `CALL { … }` and the 5.23+ `CALL () { … }` form):
  - Works: plain `MATCH … RETURN x AS y`, `ORDER BY` inside (the outer rows kept the inner order in the test), `UNION`, aggregation, an inner `LIMIT` smaller or larger than the outer one, `CALL proc() YIELD x RETURN x`, a leading `USE neo4j`.
  - Fails with `SyntaxError`: unaliased return expressions ("Expression in CALL { RETURN ... } must be aliased (use AS)"), duplicate column names, a bare `CALL db.labels()` with no `RETURN` ("RETURN * is not allowed when there are no variables in scope"), a `CYPHER …` prefix, `SHOW …` commands, and a trailing `;`.
  - Plan: `EXPLAIN` of the wrapped query and of the same query with `LIMIT 3` written inline both gave `ProduceResults → Limit → Filter → Unwind`. The planner inlines the subquery (ran). Wall times were too noisy to compare (1–16 s across runs on a 400 M `range`), so I report only the plans.
  - The manual: `CALL () {}` arrived in 5.23, and "As of Neo4j 5.23, it is deprecated to use `CALL` subqueries without a variable scope clause". The order in which outer rows enter a subquery "is not defined" (https://neo4j.com/docs/cypher-manual/5/subqueries/call-subquery/). The manual makes no promise that rows coming out of a subquery keep the inner `ORDER BY`. Order preservation is observed only (ran).
- Fetching n records: `Result.fetch(n)` returns "up to n records", and `consume()` will "Consume the remainder of this result and return the summary" (API docs). The default `fetch_size` is 1,000 records per pull (API docs, Session configuration). After `fetch(3)`, `consume()` ran until the 120 s timeout, and `Transaction.rollback()` first drains pending results (`_consume_results` in the driver's `transaction.py`) and also ran to the timeout (ran). Fetching n records bounds what Python holds, not what the server does.
- LangChain's `GraphCypherQAChain` slices after the fact: `self.graph.query(generated_cypher)[: self.top_k]` (`langchain_neo4j/chains/graph_qa/cypher.py` at `v0.11.0`). The server still returns every row.
- An eager query (`ORDER BY`, aggregation, `collect`) does its full work whatever the limit. Timeout and memory caps bound those queries, not the row limit (ran: wrapped `ORDER BY x DESC` over 400 M rows hit a 20 s timeout).

### A.5 Parsing and validating against the schema

- EXPLAIN notifications on 5.26.31 (ran), read from `ResultSummary.gql_status_objects`. `ResultSummary.notifications` is "Deprecated since version 6.0: Use gql_status_objects instead" (API docs).
  - Unknown label: `01N50`, "The label `Compny` does not exist", classification `UNRECOGNIZED`, severity `WARNING`, with a position.
  - Unknown relationship type: `01N51`.
  - Unknown property key: `01N52`.
  - Wrong direction on an existing type (`(o:Organization)-[:SUPPLIES]->(c:Company)` when only the reverse exists): only `00001` "successful completion - omitted result". No warning.
  - These warnings come from the token store ("does not exist" in the database), not from a schema. A label that exists elsewhere in the graph passes, whatever endpoints the query pairs it with.
- Errors raised by `EXPLAIN` (ran): syntax errors and undefined variables raise `neo4j.exceptions.CypherSyntaxError` with code `Neo.ClientError.Statement.SyntaxError` and a line and column in the message. `LIMIT -1` also raises a `SyntaxError` (GQL status `42001`).
- Python parsers (PyPI JSON, read 2026-10-14):
  - `CyVer` 2.0.2 (2025-09-16), CC BY-SA 4.0, needs `neo4j>=5.27`, `pandas`, `regex`; repository https://gitlab.com/netmode/CyVer. Neo4j's blog describes it as validating syntax, schema and properties against a live Neo4j schema (https://neo4j.com/?p=394429). I did not read its source. Its `neo4j>=5.27` pin conflicts with the project's `neo4j==6.3.1` only if 6.x breaks it (unverified). ShareAlike terms apply to derived code.
  - `pycypher` 0.5.9 (2017-11-01) and `libcypher-parser-python` 0.0.5 (2019-03-13): libcypher-parser bindings, unmaintained.
  - `antlr4-cypher` 0.1.2 (2025-01-14): an ANTLR4 Cypher grammar package by one author (https://github.com/qi-hua/antlr4-cypher); maturity and Cypher 5 coverage unverified.
- LangChain direction checks: `CypherQueryCorrector` is "copied from the winner's submission to the Cypher competition" and is regex-based (`langchain_neo4j/chains/graph_qa/cypher_utils.py` at `v0.11.0`). It finds node variables and labels, extracts each `(a)-[:T]->(b)` path, and checks `(start label, type, end label)` against a list of `Schema` triples. A reversed but legal direction is flipped. If neither direction is legal it returns the empty string. `GraphCypherQAChain.from_llm(validate_cypher=True)` builds the triple list from `graph.get_structured_schema["relationships"]`, which comes from `apoc.meta.data` (`neo4j_graph.py` checks that APOC is allowed). The chain refuses to construct unless `allow_dangerous_requests=True` and tells users to "narrowly scope the permissions of the database connection" (`cypher.py`).

## B. Text-to-Cypher practice

### B.1 Neo4j's Text2Cypher dataset and benchmark

- Paper: Ozsoy, Messallem, Besga, Minneci, "Text2Cypher: Bridging Natural Language and Graph Databases", arXiv 2412.10064 (https://arxiv.org/html/2412.10064v1). 25 public sources were found and 16 used. The result is 44,387 instances (39,554 train, 4,833 test). 22,093 train and 2,471 test rows have a reachable demo database.
- Dataset card (https://huggingface.co/datasets/neo4j/text2cypher-2024v1, Apache-2.0): fields `question`, `schema`, `cypher`, `data_source`, `database_reference_alias`, `instance_id`. Much of the data is synthetic, generated by GPT-4o, GPT-4-turbo, Gemini and Claude Opus over Neo4j's demo databases.
- `neo4j/text2cypher-2025v1`: same fields, 35,946 train and 4,438 test rows (datasets-server info API, read 2026-10-14). Its card has no description, and I found no paper or post describing what changed (unverified).
- Metrics (paper §4; benchmarking post, https://neo4j.com/blog/developer/benchmarking-neo4j-text2cypher-dataset/):
  - Translation-based: compare the generated Cypher text with the reference, mainly by Google-BLEU.
  - Execution-based: run both queries on the target database, turn the outputs into strings sorted lexicographically, and apply the same metrics, mainly ExactMatch. The post calls ExactMatch "very sensitive" to whitespace changes.
- Numbers: the benchmarking post gives one figure in its text. Execution ExactMatch for GPT-4o and for the fine-tuned `tomasonjo_text2cypher` was "about 30 percent". Everything else is in figures I could not read (unverified). Fine-tuning gains (paper Table 5) reach +0.34 Google-BLEU and +0.11 ExactMatch. For GPT-4o they are about +0.18 and +0.01.
- Schema format in the data: a "Node properties" block (label, then each property with its type and an example, a min/max range or "Available options"), a "Relationship properties" block, and "The relationships:" as one `(:A)-[:T]->(:B)` line per pattern (first test row, read through datasets-server).

### B.2 Schema in the prompt, examples and repair

- Schema pruning: arXiv 2505.05118 (https://arxiv.org/html/2505.05118v2) and the matching post (https://neo4j.com/blog/developer/schema-representation-in-text2cypher/) compare five formats. They are the Enhanced schema (with example values), the Base schema (no examples), Pruned by Exact-Match (labels and properties whose names appear in the question), NER-masked plus exact-match pruning, and pruning by embedding similarity.
  - Exact-match pruning cut p95 prompt length to about one sixth of the Enhanced schema and scored best for Llama-3.1-8B. The post's summary: "longer prompts actually produce lower performance".
  - Qwen2.5-7B and Gemini-1.5-Flash lost about 2% on execution with pruned prompts. The authors say larger models gain mainly on cost.
- neo4j-graphrag `Text2CypherTemplate` (`generation/prompts.py` at `1.22.0`): the sections are Task, `Schema: {schema}`, `Examples (optional): {examples}`, `Input: {query_text}`, then "Do not use any properties or relationships not included in the schema" and "Do not include triple backticks". Examples are a list of strings joined by newlines.
  - `extract_cypher()` takes the first fenced block if one exists and backtick-quotes multi-word labels, properties and types.
  - The retriever has no repair loop: a `CypherSyntaxError` becomes `Text2CypherRetrievalError`.
- `get_schema()` / `get_structured_schema()` output looks like `Node properties:` / `Person {id: INTEGER, name: STRING}` / `Relationship properties:` / `The relationships:` / `(:Person)-[:KNOWS]->(:Person)`. Both need APOC (`apoc.meta.data`, `apoc.schema.nodes`; `schema.py` at `1.22.0`).
- Repair loop with the error message: none of the three code bases read here (neo4j-graphrag `Text2CypherRetriever`, LangChain `GraphCypherQAChain`, GraphRAG) retries with the database error. I found no primary source that measured one (unverified as a practice).

### B.3 What "Cypher validity rate" means

No single definition appears in the primary sources read. The levels they use or imply:

1. Parses: `EXPLAIN` raises no `CypherSyntaxError` (A.5).
2. Valid against the graph: parses, with no `01N50`–`01N52` warnings and every `(label, type, label)` triple allowed by the ontology. Direction needs a check outside Neo4j (A.5).
3. Executes: runs without error within the timeout and memory caps.
4. Non-empty: returns at least one row.
5. Correct: the result matches a reference. Neo4j's papers score this as execution ExactMatch on sorted, stringified rows (B.1). They report only translation and execution scores, not levels 1–4.

## C. Local search: anchor, expand, rank, budget

### C.1 Microsoft GraphRAG local search

- Overview: local search "identifies a set of entities from the knowledge graph that are semantically-related to the user input". It then collects their connected entities, relationships, covariates, community reports and text chunks, and "prioritized and filtered to fit within a single context window of pre-defined size" (https://github.com/microsoft/graphrag/blob/v3.3.0/docs/query/local_search.md).
- Linking (`query/context_builder/entity_extraction.py`, `map_query_to_entities`):
  - The question text is embedded and the entity vector store is searched for `k * oversample_scaler` neighbours (k = `top_k_mapped_entities`, default 10; oversample 2).
  - Hits are mapped back to entities by id or title. Excluded names are dropped and `include_entity_names` are prepended.
  - The list is not cut back to k, so up to 20 entities come back.
  - With an empty query it takes the top k by entity `rank` instead.
- What is embedded: one string per entity, `f"{title}:{description}"` (`data_model/row_transformers.py`, `transform_entity_row_for_embedding`). Descriptions are LLM-written summaries from GraphRAG's indexing.
- Budget (`query/structured_search/local_search/mixed_context.py`, `build_context`):
  - `community_prop + text_unit_prop` must not exceed 1. The defaults in `config/defaults.py` `LocalSearchDefaults` are `text_unit_prop` 0.5, `community_prop` 0.15, `top_k_entities` 10, `top_k_relationships` 10 and `max_context_tokens` 12,000.
  - Community reports get `max_context_tokens × community_prop`, the entity, relationship and covariate tables get `1 − community_prop − text_unit_prop`, and text units get `× text_unit_prop`.
- Expansion and ranking of relationships (`query/context_builder/local_context.py`, `_filter_relationships`):
  - In-network relationships (between selected entities) come first.
  - Out-of-network relationships follow, sorted by how many selected entities share the outside neighbour (`links`), then by rank or weight.
  - Out-of-network relationships are capped at `top_k_relationships × len(selected_entities)`.
  - Entities are added one at a time until the token budget would be exceeded, then the build reverts to the previous state.
- Ranking text units (`_build_text_unit_context`):
  - For each selected entity in order, take its `text_unit_ids` (deduplicated) and count how many of the entity's relationships cite that unit (`count_relationships` in `source_context.py`).
  - Sort by (entity order ascending, relationship count descending), then add units until the next one would exceed the text-unit budget (`build_text_unit_context`, `break` on overflow).
  - A unit's rank therefore follows its entity's similarity rank first.

### C.2 Full-text and vector indexes on Community 5.26

- Full-text (ran, 5.26.31 Community):
  - `CREATE FULLTEXT INDEX entity_names FOR (n:Organization|Product) ON EACH [n.name, n.aliases] OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard-no-stop-words', `fulltext.eventually_consistent`: false}}` came up ONLINE.
  - `db.index.fulltext.queryNodes('entity_names', $q, {limit: 3})` found "TSMC" only through the `aliases` list, and also matched "taiwan semiconductor", "hynix", "H100", a fuzzy `Samsung~` and a quoted phrase.
  - `listAvailableAnalyzers()` returns 45 analyzers.
  - A malformed Lucene string (`AND (`) raised `Neo.ClientError.Procedure.ProcedureCallFailed` (a Lucene `ParseException`), so question text needs escaping before it is used as a query.
- Full-text docs (https://neo4j.com/docs/cypher-manual/5/indexes/semantic-indexes/full-text-indexes/): values must be `STRING` or `LIST<STRING>`; the default analyzer is `standard-no-stop-words` and most analyzers lowercase; `eventually_consistent` defaults to `false`; query syntax is Lucene's, with `AND`, `OR`, `NOT`, `+`, `-`, quotes and `property:` prefixes. That page does not name editions. Community support was shown by running it.
- Vector (ran):
  - `CREATE VECTOR INDEX ent_vec FOR (n:Organization) ON n.embedding OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}}` came up ONLINE as provider `vector-2.0`, with `vector.quantization.enabled: true`, `hnsw.m` 16 and `ef_construction` 100 by default.
  - `db.index.vector.queryNodes('ent_vec', 2, $v)` returned scored nodes.
  - `SEARCH x IN (VECTOR INDEX …)` is a `SyntaxError` on 5.26.
- Vector docs (https://neo4j.com/docs/cypher-manual/5/indexes/semantic-indexes/vector-indexes/): "The query is an *approximate* nearest neighbor search" (HNSW). The page notes `vector.quantization.enabled` from 5.23 and HNSW options from 5.23. It describes current Cypher 5, with options newer than 5.26 (`quantization.type` from 2026.06); see the Step 7 note on that version trap.
- ADR-0002 chose exact search for chunk vectors in pgvector. A Neo4j vector index is approximate and quantized by default, so name linking through it would not be exact. Exact cosine over a few thousand entity nodes is also possible in plain Cypher with `vector.similarity.cosine(...)`, as neo4j-graphrag's `VECTOR_EXACT_QUERY` does (`neo4j_queries.py` at `1.22.0`).

### C.3 neo4j-graphrag anchor-then-expand retrievers

- `VectorCypherRetriever(driver, index_name, retrieval_query, embedder)`: "vector similarity augmented by a Cypher query". The base query is `CALL db.index.vector.queryNodes($vector_index_name, $top_k * $effective_search_ratio, $query_vector) YIELD node, score WITH node, score LIMIT $top_k`. The user's `retrieval_query` is appended as is and can use `node` and `score` (`retrievers/vector.py`, `neo4j_queries.py` at `1.22.0`; docstring example `MATCH (node)-[:AUTHORED_BY]->(author:Author) RETURN author.name`).
- `HybridCypherRetriever`: one `CALL () { <vector part> UNION <fulltext part> }`.
  - Each part divides its scores by that index's maximum score.
  - The naive ranker then keeps `max(score)` per node, `ORDER BY score DESC, elementId(node) LIMIT $top_k`.
  - The linear ranker uses `alpha × vector + (1 − alpha) × fulltext` (default `alpha` 0.5).
  - The full-text part is `CALL db.index.fulltext.queryNodes($fulltext_index_name, $query_text, {limit: $top_k})`.
  - The same `retrieval_query` tail is appended (`neo4j_queries.py`).
- Both retrievers take `retrieval_query` from the developer, not from an LLM. The expansion is fixed Cypher, so it needs none of the guards in section A.

## Implications for Step 11

These follow from the facts above and are framed around the two settled points: each graph arm returns a ranked list of up to 10 chunk IDs to the unchanged answer prompt, and an arm that finds no anchor returns no chunks (no vector fallback). They are options, not decisions.

1. Chunk IDs as output: extracted nodes have `EVIDENCED_BY` edges to `Chunk` nodes, and extracted edges carry `chunk_ids` lists. Any arm, generated or templated, can end in a projection to chunk IDs, which matches GraphRAG's "entity → text units" step (C.1).
2. Generated Cypher guard, if a text-to-Cypher arm is built. Options to combine:
   - `EXPLAIN` with `query_type == "r"` (A.1).
   - A text deny rule for `LOAD CSV`, `TERMINATE`, `CALL dbms.`, `CALL db.create` and `SHOW` (A.1).
   - READ access mode (A.2).
   - A driver `Query(timeout=…)` (A.3).
   - `db.memory.transaction.max` set in compose, with the transient memory error caught rather than retried (A.3).
   - A row cap: the subquery wrap, which needs aliased returns (A.4), or a projection the arm owns (option 4).
3. Schema text for the prompt: APOC-based schema readers fail on this image (A.1, B.2). The generated ontology schema text from Step 7 can serve in its place. The 2024 dataset's format, one `(:A)-[:T]->(:B)` line per edge type plus property lists, is one documented layout (B.1). Pruning to terms in the question helped small models only (B.2).
4. A fixed tail: the arm could let the model write only the anchor-and-traverse part and append a fixed tail that collects evidence `chunk_ids`, ranks them and applies `LIMIT 10`, as neo4j-graphrag appends `retrieval_query` (C.3). That keeps chunk selection deterministic.
5. Direction and endpoint checks need code outside Neo4j: `EXPLAIN` does not flag a reversed edge or an invalid endpoint pair (A.5). The Step 7 ontology already lists allowed endpoints; LangChain's regex corrector shows one approach (A.5).
6. Validity metric: report the levels separately (parses, valid against the ontology, executes, non-empty, then retrieval recall on gold chunks) rather than one "validity rate" (B.3). Neo4j's execution ExactMatch needs reference Cypher, which the eval set does not have (inference).
7. Anchoring for a local-search arm. Options:
   - Full-text over `name` and `aliases` (Community, ran; needs Lucene escaping).
   - A Neo4j vector index over entity names (approximate, quantized by default).
   - Exact cosine in Cypher or in pgvector (C.2).
   GraphRAG embeds `title:description`. The project's entities have names, aliases and evidence spans but no descriptions (C.1).
8. Ranking chunks within the budget of 10: GraphRAG ranks by anchor order, then by how many of the anchor's edges a chunk supports (C.1), and Hybrid ranks by normalized max score (C.3). Both are documented rules a deterministic arm could copy.
9. No-anchor rule: GraphRAG falls back to top entities by rank when the query is empty (C.1). Under the settled rule the arm returns nothing instead, so the share of questions with no anchor becomes a number to report per class.
