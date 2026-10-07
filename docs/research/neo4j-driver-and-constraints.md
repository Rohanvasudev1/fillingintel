# Neo4j driver, constraints and test isolation (Step 7)

Research note for Step 7, written 2026-10-07, before any graph code exists. Step 7 defines the graph ontology as code over Neo4j Community, image `neo4j:5.26.31-community`, already in `docker-compose.yml`. This note checks the driver choice, what Community can enforce, how edge provenance can be stored, how to keep test data apart, how to make the read path read-only, and how to run Neo4j in CI. Sources: the Neo4j Cypher Manual 5, Operations Manual 5 and Python Driver Manual and API docs 6.4 at neo4j.com/docs (fetched 2026-10-07); PyPI JSON metadata for `neo4j` and `neo4j-driver`; the driver's 6.x changelog on its GitHub wiki; the image's Dockerfile in github.com/neo4j/docker-neo4j-publish (`5.26.31/trixie/community/Dockerfile`), read with `gh api`; and GitHub Docs from the source Markdown in github.com/github/docs. Claims marked "(ran)" were checked against a throwaway `neo4j:5.26.31-community` container started with `NEO4J_AUTH=none` on ports 17474/17687 (removed afterwards; the project's own Neo4j container was not touched), using `uv run --no-project --python 3.11 --with neo4j==6.4.0`, never in the project. Anything not confirmed from a primary source is marked "unconfirmed".

A version trap: since Neo4j 2025.06 the "Cypher Manual 5" (`/docs/cypher-manual/5/`) documents the Cypher 5 *language* as it runs on current servers, not server 5.26. It says "As of Neo4j 2025.06, Cypher 5 is in a frozen state" (https://neo4j.com/docs/cypher-manual/5/introduction/). Pages there list features newer than 5.26, such as the `VECTOR` and `UUID` property types. Each claim below that depends on the server version is checked against the manual's "Additions, deprecations, removals" page, which dates every change by server release (https://neo4j.com/docs/cypher-manual/5/deprecations-additions-removals-compatibility/), or was run against 5.26.31. The Operations Manual has a separate 5.26 (LTS) version at `/docs/operations-manual/5/`.

## Summary and recommendation

- Driver: `neo4j==6.4.0` (released 2026-10-05). The 6.x series supports Neo4j 4.4, 5.x, 2025.x and 2026.x servers and needs Python 3.10 or newer. Its only runtime dependency is `pytz`. `neo4j-driver` is the deprecated old name and gets no releases from 6.0 on. Adding it needs approval under "ask before adding a dependency" (section 1).
- Community 5.26 has node and relationship property uniqueness constraints, single or composite, and nothing else. Existence, property type, node key and relationship key constraints are Enterprise-only; on 5.26.31 Community each fails with `Neo.DatabaseError.Schema.ConstraintCreationFailed` "... requires Neo4j Enterprise Edition" (ran, section 2).
- `CREATE CONSTRAINT name IF NOT EXISTS` is a silent no-op when *any* constraint already has that name, even one on a different property. It returns only an informational notification. Changing a constraint's definition without renaming it therefore leaves the old one in force. Verify with `SHOW CONSTRAINTS` after applying (ran, section 2.3).
- Each uniqueness constraint creates a RANGE backing index with the same name. Uniqueness ignores entities missing any constrained property, so on Community nothing forces a property to exist; the loader must check that (section 2).
- A relationship always connects one source node to one target node. Neo4j has no hyperedges and no relationship-to-relationship or relationship-to-node links. Neo4j's modelling guide offers two options: put the data on the relationship as properties, or reify it as an intermediate node (section 3).
- A relationship property can hold a list of strings. Lists must be homogeneous, of simple types, with no nulls; an empty list is allowed. No index can find a relationship by one element of a list property: `'x' IN r.source_chunk_ids` plans as a full relationship-type scan with a filter, even with a range index on the property (ran, sections 3 and 4).
- Community has exactly one standard database plus `system`. `CREATE DATABASE` and `CREATE OR REPLACE DATABASE` fail with `UnsupportedAdministrationCommand` (ran). Test isolation needs a separate container or port, or a guarded wipe of the one database (section 5).
- Read-only: Community can create users but has no roles or privileges (`SHOW ROLES` and `GRANT ROLE` fail with `UnsupportedAdministrationCommand`, ran), so a read-only database user is not available. On 5.26.31 Community, writes inside a READ transaction are rejected with `Neo.ClientError.Statement.AccessMode` for `execute_query(routing_=READ)`, `session(default_access_mode=READ_ACCESS)` and `execute_read`, over both `bolt://` and `neo4j://` (ran). The driver docs say not to rely on this for access control. An `EXPLAIN` gives `summary.query_type` (`r`, `rw`, `w`, `s`) without running the query (section 6).
- Dynamic labels and types: `$(...)` in `MATCH`, `CREATE` and `MERGE` arrived in 5.26; in `SET` and `REMOVE` in 5.24. On 5.26 a `MATCH (n:$($label))` plans as `AllNodesScan` (ran; documented for 5.26 to 2025.07). Static labels from an allowlist are the better choice for the ontology (section 6.3).
- CI: run the same image as a service container with `NEO4J_AUTH`, ports 7687 (and 7474 if wanted), and a health check of `wget -q --spider http://localhost:7474` or `cypher-shell ... 'RETURN 1'`. Both tools are in the image. A local cold start took 22 s to HTTP and about 27 s to Bolt (ran, section 7).

## 1. The Python driver

### 1.1 Version and compatibility

- PyPI (https://pypi.org/pypi/neo4j/json, read 2026-10-07): latest is `6.4.0`, uploaded 2026-10-05T15:37:43Z, `requires_python >=3.10`, classifiers for Python 3.10 to 3.15. The 6.x line so far: 6.0.0 (2025-09-30), 6.0.1, 6.0.2, 6.0.3, 6.1.0 (2026-01-12), 6.2.0 (2026-05-04), 6.3.0 (2026-08-28), 6.3.1 (2026-09-15), 6.4.0. The 5.x line still gets patches: 5.28.7 on 2026-10-05.
- Server compatibility. The Python Driver Manual 6 install page says "The latest driver in the `6.x` series supports connection to Neo4j instances version 4.4.x, 5.x, 2025.x, and 2026.x", and requires Python >= 3.10 (https://neo4j.com/docs/python-manual/current/install/). The PyPI description points to a supported-versions matrix at https://neo4j.com/developer/kb/neo4j-supported-versions/, which now redirects to a support portal page that would not render; the matrix itself is unconfirmed. The install-page sentence is enough for 5.26.
- Connected and ran queries with 6.4.0 against `Neo4j/5.26.31`, edition `community` per `dbms.components()` (ran).
- Driver 6.0 breaking changes, from the 6.x changelog (https://github.com/neo4j/neo4j-python-driver/wiki/6.x-changelog): minimum Python 3.10; `session.read_transaction()` and `write_transaction()` removed in favour of `execute_read()` and `execute_write()`; `Bookmark` removed; the `trust` option removed; drivers and sessions are no longer closed in `__del__`, so code must use `with` or `.close()`. Later 6.x minors add Python 3.14 and 3.15, Pandas 3 and PyArrow versions, Bolt 6.1 with UUID values (6.3) and an HTTP Query API preview (6.4).

### 1.2 Dependencies

- `requires_dist` for 6.4.0: `pytz` only, plus optional extras `numpy`, `pandas`, `pyarrow` and `http` (aiohttp, urllib3) (PyPI JSON). Installing it in a scratch environment pulled `neo4j 6.4.0` and `pytz 2026.4`, two packages (ran). Lock-file effect on this project not checked, since `uv add` was out of scope.
- `neo4j-rust-ext` is an optional drop-in package for a "3x to 10x speedup" (install page). Not needed at this graph size.

### 1.3 The old package name

- `neo4j-driver` on PyPI is at 5.28.7, and its description says it "is deprecated and will stop receiving updates starting with version 6.0.0. Please install `neo4j` instead" (https://pypi.org/pypi/neo4j-driver/json). The `neo4j` README repeats this (https://pypi.org/project/neo4j/).

### 1.4 Telemetry

- `telemetry_disabled` (default `False`): "the driver will send anonymous usage statistics to the server it connects to if the server requests those", namely which API ran a query (`execute_query`, `execute_read` and so on), with no arguments or identifiers (https://neo4j.com/docs/api/python-driver/current/api.html#telemetry-disabled). It goes to the Neo4j server, not to a third party. Whether the 5.26 server forwards it anywhere is unconfirmed. Setting `telemetry_disabled=True` costs nothing.

## 2. Constraints in Neo4j 5.26

### 2.1 Which edition has what

The constraints overview lists property uniqueness as available in Community, and existence, property type and key constraints as Enterprise Edition (https://neo4j.com/docs/cypher-manual/5/constraints/). Checked on 5.26.31 Community (ran):

| Constraint | Community 5.26 | Cypher (Community form) or result on Community |
|---|---|---|
| Node property uniqueness, one property | yes | `CREATE CONSTRAINT company_ticker IF NOT EXISTS FOR (c:Company) REQUIRE c.ticker IS UNIQUE` |
| Node property uniqueness, composite | yes | `CREATE CONSTRAINT period_key IF NOT EXISTS FOR (p:Period) REQUIRE (p.ticker, p.fiscal_period) IS UNIQUE` |
| Relationship property uniqueness (since 5.7) | yes | `CREATE CONSTRAINT edge_id IF NOT EXISTS FOR ()-[r:MENTIONS]-() REQUIRE r.edge_id IS UNIQUE` |
| Node property existence | no | `ConstraintCreationFailed`: "Property existence constraint requires Neo4j Enterprise Edition" |
| Relationship property existence | no | same error |
| Property type (since 5.9; `LIST<STRING NOT NULL>` since 5.10) | no | "Property type constraint requires Neo4j Enterprise Edition" |
| Node key | no | "Node Key constraint requires Neo4j Enterprise Edition" |
| Relationship key | no | "Relationship Key constraint requires Neo4j Enterprise Edition" |

Syntax and version notes come from https://neo4j.com/docs/cypher-manual/5/constraints/create-constraints/. Relationship uniqueness dates from 5.7 on the additions page. A constraint name can be a parameter (`CREATE CONSTRAINT $name ...`; "Create a constraint with a parameter", same page).

### 2.2 Semantics that matter for the loader

- Missing properties: "Nodes or relationships missing one or more of the specified properties are not subject to this rule" (create-constraints page). A composite uniqueness constraint does not stop a node that lacks one of the properties. Without existence constraints, the loader has to check required properties itself.
- Existing duplicates: creating a uniqueness constraint over data that already violates it fails (create-constraints page, "Creating constraints when there exists conflicting data will fail"). Apply constraints before loading.
- Backing index: "Property uniqueness constraints and key constraints are backed by range indexes", with the same name; dropping the constraint drops the index (create-constraints page). Seen on 5.26.31: each constraint has a `RANGE` index with `owningConstraint` set to the constraint name, besides the two default `LOOKUP` indexes (ran). A constraint cannot be created while a plain index exists on the same schema: "There already exists an index. A constraint cannot be created until the index has been dropped."
- One constraint per schema: a relationship key on the same type and property as an existing relationship uniqueness constraint failed with `ConstraintAlreadyExists` before the edition check ran (ran). Not a concern on Community, since keys are unavailable.

### 2.3 `IF NOT EXISTS`

- The manual: with `IF NOT EXISTS`, "no error is thrown and ... no constraint is created if any other constraint with the given name, or another constraint on the same constraint type and schema, or both, already exists"; since 5.17 an informational notification shows the blocking constraint (create-constraints page, "Handling existing constraints when creating a constraint").
- Seen on 5.26.31 (ran):
  - Same name, same definition: no-op, notification "`CONSTRAINT company_ticker FOR (e:Company) REQUIRE (e.ticker) IS UNIQUE` already exists."
  - Different name, same definition: no-op, same notification naming the existing constraint. The new name is not created.
  - Same name, different property (`c.cik` instead of `c.ticker`): no-op with the same notification. The `cik` constraint does not exist afterwards.
  - Same name without `IF NOT EXISTS`: `Neo.ClientError.Schema.ConstraintWithNameAlreadyExists`.
- So a schema module that only runs `CREATE ... IF NOT EXISTS` cannot detect drift. Read `SHOW CONSTRAINTS` back and compare name, type, entity type, labels or types, and properties against the code. The driver exposes the notifications on `summary.notifications`, which is a cheaper signal but not a full check.

### 2.4 Listing constraints

- `SHOW CONSTRAINTS` returns `id, name, type, entityType, labelsOrTypes, properties, ownedIndex, propertyType` by default; `SHOW CONSTRAINTS YIELD *` adds the rest, including `createStatement`. `type` values include `UNIQUENESS`, `RELATIONSHIP_UNIQUENESS`, `NODE_KEY`, `RELATIONSHIP_KEY`, `NODE_PROPERTY_EXISTENCE`, `RELATIONSHIP_PROPERTY_EXISTENCE`, `NODE_PROPERTY_TYPE` and `RELATIONSHIP_PROPERTY_TYPE`. Filters: `SHOW UNIQUENESS CONSTRAINTS`, `WHERE`, `YIELD` (https://neo4j.com/docs/cypher-manual/5/constraints/list-constraints/).
- Sample row on 5.26.31: `{'name': 'rel_uniq', 'type': 'RELATIONSHIP_UNIQUENESS', 'entityType': 'RELATIONSHIP', 'labelsOrTypes': ['MENTIONS'], 'properties': ['edge_id'], 'ownedIndex': 'rel_uniq'}` (ran). `SHOW INDEXES YIELD name, type, owningConstraint` confirms the backing index.

## 3. Relationships and edge provenance

### 3.1 No hyperedges

- Neo4j's glossary: "Relationships connect a source node to a target node, hold data in properties, and are classified by type" (https://neo4j.com/docs/getting-started/graph-database/).
- The modelling guide: "In a mathematical graph, this can be solved with a hyperedge, i.e. a relationship that connects more than two nodes. This is not supported in Neo4j but can be solved by using an intermediary node." Its example offers either a property on the `WORKED_AT` relationship or an intermediate employment node (https://neo4j.com/docs/getting-started/data-modeling/modeling-designs/, "Intermediate nodes").
- Structural types (`NODE`, `RELATIONSHIP`, `PATH`) "cannot be stored as properties" (https://neo4j.com/docs/cypher-manual/5/values-and-types/property-structural-constructed/). A relationship cannot point at a node or relationship through a property either; it can only hold an identifier string.

### 3.2 Provenance on the relationship vs an intermediate node

Neither pattern is named as "provenance" in Neo4j's docs; the trade-offs below combine the modelling guide with the index behaviour in section 4.

- List property on the relationship, for example `(:Company)-[:SUPPLIES {source_chunk_ids: ['0001045810-26-000012:0042', ...]}]->(:Company)`:
  - Traversal stays one hop: `MATCH (a:Company {ticker: $t})-[r:SUPPLIES]->(b) RETURN b, r.source_chunk_ids`.
  - Citation lookup by chunk ("which edges came from chunk X?") cannot use an index. It scans every relationship of that type and filters (ran, section 4.2). At this corpus size (24 filings) that is cheap.
  - Nothing in Community can force the list to exist or be non-empty (section 2.1). The loader has to reject edges without it, as invariant 4 requires.
  - Merging evidence for an existing edge means rewriting the list, for example `SET r.source_chunk_ids = [x IN r.source_chunk_ids WHERE NOT x IN $new] + $new`.
- Intermediate node, for example `(:Company)-[:SUBJECT_OF]->(:Claim {claim_id})-[:OBJECT]->(:Company)` and `(:Claim)-[:CITES]->(:Chunk {chunk_id})`:
  - Every traversal gains a hop. Path queries for multi-hop questions become `(a)-[:SUBJECT_OF]->(:Claim)-[:OBJECT]->(b)` instead of `(a)-[:SUPPLIES]->(b)`, and variable-length patterns over the semantic relation are harder to write.
  - "Which claims cite chunk X?" becomes an index seek on a unique `Chunk.chunk_id` followed by an expand.
  - Per-citation data (offsets, extraction model, confidence) fits on `CITES` relationships.
  - A `Chunk` node gives a uniqueness constraint on `chunk_id`, so provenance can be checked structurally ("every Claim has at least one CITES") by a query, though still not by a Community constraint.
- A middle option keeps the direct relationship for traversal and adds `Chunk` nodes linked to entities (`(:Chunk)-[:MENTIONS]->(:Company)`), with the edge's `source_chunk_ids` list naming chunks that exist as nodes. The loader checks that each listed ID matches a `Chunk` node. That check is application code.

## 4. List properties

### 4.1 Allowed values

- "Homogeneous lists of simple types can be stored as properties (with the exception of VECTOR types, which cannot be stored in lists) ... Lists stored as properties cannot contain null values." Maps and general lists cannot be stored (https://neo4j.com/docs/cypher-manual/5/values-and-types/property-structural-constructed/). That page reflects current Cypher 5; the list rules below were checked on 5.26.31.
- On 5.26.31 (ran):
  - `['0001:0001', '0001:0002']` on a relationship: stored; `valueType()` gives `LIST<STRING NOT NULL> NOT NULL`.
  - `[]`: stored; `valueType()` gives `LIST<NOTHING> NOT NULL`, size 0. An empty list loses its element type, which matters for anyone reading it back as typed.
  - `['a', 1]`: `Neo.ClientError.Statement.TypeError`, "Neo4j only supports a subset of Cypher types for storage as singleton or array properties."
  - `['a', null]`: TypeError, "Collections containing null values can not be stored in properties."
  - `[['a']]`: TypeError, "Collections containing collections can not be stored in properties."
  - `{a: 1}`: TypeError, "Property values can only be of primitive types or arrays thereof."

### 4.2 Indexing and constraining list properties

- Range indexes support equality, list membership (`n.prop IN list`, meaning a scalar property is one of the given values), existence, range and prefix predicates (https://neo4j.com/docs/cypher-manual/5/indexes/search-performance-indexes/create-indexes/). No predicate tests whether a list property contains a value. Text indexes "only solve predicates operating on STRING values" (same page).
- On 5.26.31 with `CREATE INDEX rel_ids FOR ()-[r:MENTIONS]-() ON (r.source_chunk_ids)` online (ran):
  - `WHERE '0001:0001' IN r.source_chunk_ids` plans as `DirectedRelationshipTypeScan` then `Filter`: the index is not used.
  - `WHERE r.source_chunk_ids = ['0001:0001','0001:0002']` plans as `DirectedRelationshipIndexSeek`: whole-list equality can use it, which is rarely useful.
- Constraints on list properties: a uniqueness constraint on a list property would compare whole lists (unconfirmed; not run). The only constraint that checks a list's element type, `IS :: LIST<STRING NOT NULL>`, is Enterprise-only (section 2.1).

## 5. One database and test isolation

### 5.1 Community database count

- "Installations of Community Edition can have exactly one standard database. Installations of Enterprise Edition can have any number of standard databases." The default is named `neo4j` (https://neo4j.com/docs/operations-manual/5/database-administration/).
- On 5.26.31 Community, `SHOW DATABASES` lists `neo4j` (standard) and `system` (system). `CREATE DATABASE testdb` and `CREATE OR REPLACE DATABASE neo4j` both fail with `Neo.ClientError.Statement.UnsupportedAdministrationCommand` (ran).

### 5.2 Options for test data

- Separate instance: a second container on other host ports (for example 17687), or the CI service container, which starts empty for each job. This is the only way to get a fully separate database on Community. It mirrors the per-session throwaway Postgres schema that the existing DB tests use.
- Label namespacing (for example an extra `:Test_<run>` label on every node): possible, but every query and every constraint would need to carry it, and uniqueness constraints are per label, so test and real nodes with the same `ticker` would still collide under a shared `Company` constraint. Not a good fit.
- Guarded wipe: `MATCH (n) DETACH DELETE n` deletes all nodes and relationships but "is not suitable for deleting large amounts of data, nor does it delete indexes and constraints"; for large data, use `MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS` (https://neo4j.com/docs/cypher-manual/5/clauses/delete/). Dropping constraints needs `SHOW CONSTRAINTS` then `DROP CONSTRAINT name` per row. A guard should refuse to wipe unless the target is known to be a test instance, for example a URI or port allowlist or a marker node the test fixture created.
- Timing on 5.26.31, local Mac: `MATCH (n) DETACH DELETE n` over 10,000 nodes and 5,000 relationships took 0.56 s; the `IN TRANSACTIONS OF 1000 ROWS` form took 0.49 s (ran). Graphs in Step 7 tests will be far smaller.
- `CALL { ... } IN TRANSACTIONS` "is only allowed in implicit transactions"; default batch 1,000 rows (https://neo4j.com/docs/cypher-manual/5/subqueries/subqueries-in-transactions/). `Driver.execute_query` uses a managed transaction, so it cannot run these; the API docs say to use `Session.run()` (https://neo4j.com/docs/api/python-driver/current/api.html#neo4j.Driver.execute_query). Through `session.run(...)` it worked (ran). Through `execute_query` it also returned without error on 5.26.31, but only on an already empty graph (ran), so that run proves nothing; follow the docs.

## 6. Driver idioms

### 6.1 `execute_query` vs sessions

- `Driver.execute_query(query, parameters_=None, routing_=RoutingControl.WRITE, database_=None, ..., **kwargs)` runs one query in a managed transaction function with retries and returns an `EagerResult` (records, summary, keys). The docs show it as equivalent to a session plus `execute_read`/`execute_write` with `unit_of_work(query_.metadata, query_.timeout)` (API docs, `Driver.execute_query`). Keyword arguments without a trailing underscore become query parameters.
- `Session.execute_read(fn, ...)` and `execute_write(fn, ...)` run a callback that can issue several queries in one transaction, retried on transient failure (https://neo4j.com/docs/python-manual/current/transactions/). Sessions are not thread-safe; the driver is.
- Pass `database_="neo4j"` (or `database=` on sessions) explicitly; the manual's examples always do, and it saves a round trip to resolve the home database (https://neo4j.com/docs/python-manual/current/query-simple/).
- `query` is typed `LiteralString | Query`, so type checkers flag f-string queries (API docs).

### 6.2 Parameters, labels and injection

- Parameters work for literals, expressions and IDs, and since 5.26 for labels and types when written dynamically. They "cannot be used for ... Property keys; Relationship types; `MATCH (n)-[:$param]->(m)` is invalid. Node labels; `MATCH (n:$param)` is invalid" (https://neo4j.com/docs/cypher-manual/5/syntax/parameters/).
- Where structure must vary, the driver manual says: "enclose the dynamic values in backticks and escape them yourself to protect against Cypher injections", doubling backticks and also handling the Unicode escape `\u0060` (https://neo4j.com/docs/python-manual/current/query-advanced/). It also suggests APOC, which this image does not load.
- For a fixed ontology, the simplest safe pattern is a closed allowlist (an enum of labels and relationship types in code), with each query string built only from allowlist members, validated with a pattern such as `^[A-Z][A-Za-z0-9_]*$`, and backtick-quoted anyway.

### 6.3 Dynamic labels on 5.26

- Additions page: 5.24 added `SET n:$(label)` and `REMOVE n:$(label)`; 5.26 added `MATCH (n:$($label))`, `CREATE` and `MERGE` with `$(...)` for labels and relationship types.
- On 5.26.31 (ran): `MERGE (n:$($l) {ticker: 'INTC'})`, `SET n:$($l)`, and `MERGE (a)-[r:$($t)]->(b)` all worked with parameters. A label value of ``Bad`Label) DETACH DELETE n //`` was stored as a literal label name, not executed, so `$(...)` is injection-safe; it does not stop junk labels, so an allowlist is still needed.
- Performance: the MATCH page's caveat table says that for 5.26 to 2025.07 "The Cypher planner is not able to leverage indexes ... and must instead utilize the AllNodesScan operator" (https://neo4j.com/docs/cypher-manual/5/clauses/match/, "Performance caveats"). `EXPLAIN MATCH (n:$($l)) RETURN n` planned `AllNodesScan` then `Filter` (ran). Prefer static labels in read queries.

### 6.4 Read-only access on Community

- Users and roles. Operations Manual 5: role-based access control "applies to Enterprise Edition. A limited set of user management functions are also available in Community Edition" (https://neo4j.com/docs/operations-manual/5/authentication-authorization/). The built-in roles table marks create/delete user and change password as available in Community, and create/drop roles, assign roles and grant/deny/revoke privileges as not (https://neo4j.com/docs/operations-manual/5/authentication-authorization/built-in-roles/; read from the table's HTML, so treat the per-row marks as likely rather than certain). On 5.26.31: `CREATE USER` worked; `SHOW ROLES` and `GRANT ROLE` failed with `UnsupportedAdministrationCommand`; `SHOW USERS` returns `roles: None` (ran). A Community user cannot be limited to reads.
- Access mode. The driver manual: "Although executing a write query in read mode results in a runtime error, you should not rely on this for access control ... There is no security guarantee that a write query submitted in read mode will be rejected" (https://neo4j.com/docs/python-manual/current/transactions/). The API docs add that "depending on the server version and settings, the server or cluster might allow a write-statement to be executed even when `neo4j.READ_ACCESS` is chosen" (API docs, `default_access_mode`).
- What 5.26.31 Community does (ran): `CREATE` through `execute_query(routing_=RoutingControl.READ)`, through `session(default_access_mode=READ_ACCESS).run()`, and inside `execute_read`, plus the write procedure `CALL db.createLabel(...)` inside `execute_read`, all failed with `Neo.ClientError.Statement.AccessMode`, "Writing in read access mode not allowed. Attempted write to neo4j", on both `bolt://` and `neo4j://`. No node was written. This is a useful guard for the read path, but Neo4j does not promise it.
- `EXPLAIN` check: `ResultSummary.query_type` is `'r'`, `'rw'`, `'w'` or `'s'` (API docs). `EXPLAIN MERGE (n:X {a:1})` gave `w`, `EXPLAIN MATCH (n) RETURN n` gave `r` (ran). A read path can `EXPLAIN` any generated query first and refuse anything but `r`. This matters only if Step 7 or later runs generated Cypher (text-to-Cypher); for hand-written templates, tests suffice.
- Whole-database read-only: `server.databases.read_only` ("List of databases for which to prevent write queries") and `server.databases.default_to_read_only` are dynamic settings with no Enterprise marker (https://neo4j.com/docs/operations-manual/5/configuration/configuration-settings/). They block the loader too, so they suit a separate read-only serving instance, not the shared dev instance. In Docker they are set as `NEO4J_server_databases_read__only=neo4j` (underscore doubled, period becomes underscore; https://neo4j.com/docs/operations-manual/5/docker/configuration/). Not run.

### 6.5 Timeouts

- Driver: `unit_of_work(timeout=...)` for transaction functions and `Query(text, metadata=None, timeout=None)` for `execute_query` and `Session.run`. Timeout is in seconds; "Transactions that execute longer than the configured timeout will be terminated by the database"; it overrides the server's `db.transaction.timeout`; `0` means no limit and `None` uses the server default (API docs, `Query` and `unit_of_work`). The minimum is 1 ms (transactions page).
- On 5.26.31 a query under `Query(..., timeout=0.5)` failed with `Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration` after 1.99 s wall time (ran). Termination is checked periodically, so expect some overrun; the exact check interval is unconfirmed.
- Server: `db.transaction.timeout`, dynamic, default `0s` (no limit) (configuration settings page). `db.lock.acquisition.timeout` defaults to `0s` as well.

## 7. Neo4j in GitHub Actions

- Image facts (Dockerfile at `5.26.31/trixie/community/Dockerfile`, github.com/neo4j/docker-neo4j-publish): based on `debian:trixie-slim` with Temurin 21; installs `wget`, `jq`, `procps` and `tini` and purges `curl` after build; `EXPOSE 7474 7473 7687`; `VOLUME /data /logs`; `NEO4J_HOME=/var/lib/neo4j` with its `bin` on `PATH`. In the running container `which` found `/var/lib/neo4j/bin/cypher-shell` (Cypher-Shell 5.26.31) and `/usr/bin/wget`, and no `curl` (ran).
- `NEO4J_AUTH=neo4j/<password>` sets the initial password, or `NEO4J_AUTH=none` disables auth. The user name cannot be changed from `neo4j`. Since 5.13 the minimum password length is 8 characters. `NEO4J_AUTH` has no effect on a mounted `/data` that already has auth (https://neo4j.com/docs/operations-manual/5/docker/introduction/). A CI service container starts with empty `/data`, so it always applies there.
- Ports: 7687 Bolt, 7474 HTTP (Browser). Map them with `ports:` when the job runs on the runner, as the Postgres service in `ci.yml` already does (GitHub Docs, `content/actions/tutorials/use-containerized-services/use-docker-service-containers.md`, "Mapping Docker host and service container ports").
- Health check: GitHub's own Postgres example uses `options: --health-cmd pg_isready --health-interval 10s --health-timeout 5s --health-retries 5` (`create-postgresql-service-containers.md`); the runner waits for the container to report healthy. For Neo4j, two candidates, both available in the image:
  - `--health-cmd "wget -q --spider http://localhost:7474 || exit 1"`: cheap, but HTTP came up about 5 s before Bolt answered in the local run.
  - `--health-cmd "cypher-shell -u neo4j -p <password> 'RETURN 1'"`: tests Bolt directly but starts a JVM each time (a few seconds per probe).
  Neither has been run inside GitHub Actions; both were run inside the container locally (ran).
- Start-up time: on this Mac, a cold `neo4j:5.26.31-community` container answered HTTP after 22 s and `cypher-shell 'RETURN 1'` after about 27 s (ran). On `ubuntu-latest` runners, unconfirmed; allow for 60 s or more, for example `--health-interval 5s --health-retries 20`. Image pull time is extra.
- Plugins: none needed. APOC and GDS load only through `NEO4J_PLUGINS`; leave it unset.
- Suggested service (for review, not applied):

```yaml
    services:
      neo4j:
        image: neo4j:5.26.31-community
        env:
          NEO4J_AUTH: neo4j/filingintel-ci
        options: >-
          --health-cmd "wget -q --spider http://localhost:7474 || exit 1"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 24
        ports:
          - 7687:7687
```

  The password is a CI-only test value, like the Postgres one in `ci.yml`. Tests should also retry `driver.verify_connectivity()` for a few seconds, given the HTTP-before-Bolt gap.

## Recommendations for Step 7

1. Driver pin: `neo4j==6.4.0` (Python >= 3.10, supports 5.26; pulls only `pytz`). Needs the user's approval as a new dependency. If a two-day-old release is too fresh, `6.3.1` (2026-09-15) has the same server support. Never `neo4j-driver`. Construct the driver with `telemetry_disabled=True` and always pass `database_="neo4j"`.
2. Constraints: only uniqueness exists on Community. Use one-property uniqueness on each node's natural key (for example `Company.ticker`, `Chunk.chunk_id`), composite uniqueness where the key is compound (for example `(ticker, fiscal_period)`), and relationship uniqueness on an `edge_id` if edges get stable IDs. Apply them before loading, then read `SHOW CONSTRAINTS` back and fail if any name, type, label or property differs from the code, because `IF NOT EXISTS` hides drift. Existence, type and "provenance present" rules live in the loader's validation and in tests, since Community cannot enforce them.
3. Provenance: a `source_chunk_ids` list of strings on each relationship, validated by the loader (non-empty, every ID a real chunk) to meet invariant 4, is the simplest fit for one-hop traversal. If Step 7 needs reverse lookup from a chunk to its edges, or per-citation data, add `Chunk` nodes, or reify as intermediate nodes; this is the decision to settle in grilling.
4. Test isolation: a separate Neo4j for tests (the CI service container, and locally a second container on another port or a dedicated test URI), never the dev database. Inside it, a fixture runs a guarded `MATCH (n) DETACH DELETE n` and drops constraints between tests, and refuses to run unless the URI matches a test allowlist. Database tests skip without the Neo4j env vars, as the Postgres ones do.
5. Read-only: run all query-path Cypher through `execute_read` (or `routing_=RoutingControl.READ`). On 5.26 Community this rejects writes, but treat it as a guard, not access control. Add a test that a write inside the read path fails. If generated Cypher ever reaches the database, also `EXPLAIN` it and refuse any `query_type` other than `r`. A truly read-only user needs Enterprise. Set a driver-side timeout (`Query(..., timeout=...)` or `unit_of_work(timeout=...)`) on every read.
6. Labels: keep labels and relationship types in a closed enum and write them statically into query text. Do not use `$(...)` in read queries on 5.26, since it forces an `AllNodesScan`.
