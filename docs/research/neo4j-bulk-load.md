# Bulk loading the graph into Neo4j Community (Step 10)

Research note for Step 10, written 2026-10-13, before the grilling. Step 10 loads the whole graph (structural nodes from Postgres, extracted nodes and edges from `data/extract/`, resolved through the alias map) through the guarded write path in `graph/write.py`. This note covers how to batch that load, how to size transactions, how MERGE behaves, how to update the parallel evidence lists, and where Eager operators appear. It builds on `neo4j-driver-and-constraints.md` (Step 7) and does not repeat it: constraint types per edition, list property rules, `IF NOT EXISTS` drift and dynamic-label scans are there.

Versions: server image `neo4j:5.26.31-community` (`docker-compose.yml`, both `neo4j` and `neo4j-test`), driver `neo4j==6.3.1` (`pyproject.toml`). Sources: the Neo4j Cypher Manual 5, Operations Manual 5, Status Codes 5, Python Driver Manual and the driver API docs for 6.3 at neo4j.com/docs; the driver's 6.x changelog on its GitHub wiki. All read 2026-10-13. The Step 7 note's version trap still applies: Cypher Manual 5 documents the Cypher 5 language on current servers, not server 5.26, so every version-sensitive claim below was also run. Claims marked "(ran)" were checked against a throwaway `neo4j:5.26.31-community` container (`NEO4J_AUTH=none`, ports 17687 and 17688, removed afterwards; the project's `neo4j` and `neo4j-test` containers were not touched) with `uv run --no-project --python 3.11 --with neo4j==6.3.1`. Anything not confirmed is marked "unconfirmed".

Scale, counted from the 25 local files in `data/extract/` (24 filings plus NVDA FY2026 run 1, 14.7 MB): 8,473 node records, 13,231 edge records and 3,828 flags, before resolution merges any of them. The largest file is 2,191 records (1.2 MB). Evidence per record is at most 17 entries. Chunk, Filing, Company and Period nodes add the structural part (one Chunk node per chunk).

## Summary

- **Batching:** `UNWIND $rows AS row` over a list of maps is the documented pattern ([UNWIND](https://neo4j.com/docs/cypher-manual/5/clauses/unwind/), "Creating nodes from a list parameter"; [driver performance page](https://neo4j.com/docs/python-manual/current/performance/), "Batch data creation"). Labels and edge types stay static, in backticks, from the ontology allowlist. A `MERGE (n:$($label) ...)` also plans as `AllNodesScan` on 5.26.31, not only `MATCH` (ran), so the current choice stands.
- **Transaction size is not a constraint at this scale.** 10,000 node MERGEs plus 15,000 edge MERGEs carrying four-entry evidence lists ran in one `execute_write` in 5.5 s with default memory settings (ran). The whole corpus fits in one transaction; one per filing is comfortable.
- **Memory defaults on 5.26.31 Community** (ran, `SHOW SETTINGS`): `db.memory.transaction.max` = `0B` (no per-transaction limit), `db.memory.transaction.total.max` = `0B`, `dbms.memory.transaction.total.max` = 686 MiB (the docs give 70% of heap as the default), `db.transaction.timeout` = `0s`. They are dynamic, but Community cannot change them at runtime: `dbms.setConfigValue` does not exist there (ran; [dynamic settings](https://neo4j.com/docs/operations-manual/5/configuration/dynamic-settings/) is an Enterprise page). Set them through `NEO4J_*` environment variables in compose.
- **A memory-limit failure is retried.** Exceeding the limit raises `Neo.TransientError.General.MemoryPoolOutOfMemoryError`, a transient error, so `execute_write` retried it three times over about 28 s before failing (ran with `db.memory.transaction.max=2MiB`). A deterministic failure costs the full `max_transaction_retry_time` (30 s default).
- **`CALL { } IN TRANSACTIONS` works only through `session.run()`.** Inside `execute_write` and inside `begin_transaction()` it fails with `Neo.DatabaseError.Transaction.TransactionStartFailed` (ran). It also commits batch by batch, which breaks the write path's "all or nothing" rule. Not needed here.
- **MERGE:** with the uniqueness constraints from Step 7, node MERGE plans as `NodeUniqueIndexSeek(Locking)` and stays single under 8 concurrent writers; without a constraint, the same race made 8 nodes (ran). Edge MERGE between two bound nodes plans `LockingMerge` and made one edge under the same race (ran), which the MERGE page explains by locking both end nodes. One edge per (start, type, end) needs no relationship constraint. MERGE with a `null` value in its pattern fails (ran), so a `role` used as edge identity must be non-null.
- **Eager appears in the current write path** (ran): every node MERGE and edge MERGE in `graph/write.py` plans an `Eager` operator, because `SET n += row.props` writes "some property" the planner cannot name, and because the edge query reads and writes `chunk_ids` in one statement. Writing each property by name removes it.
- **Parallel lists:** a list comprehension over indexes replaces one chunk's entry in all four lists in one statement (ran). Inside one UNWIND, two rows for the same edge both read the state from before the statement, so the second overwrites the first: one evidence entry was lost (ran). Fold rows per edge in Python before writing.
- **Recommendation:** compute each node's and edge's final state in Python (alias map applied, the Step 8 replace and most-confident rules folded across all filings), then write it with `MERGE` on the key and named `SET`s, one transaction per label group or per filing, through `execute_write`. Details and the conflicts with the current design are in section 7.

## 1. UNWIND batching

### 1.1 Parameter shape

- The driver manual's batching example passes a list of maps and unwinds it: `numbers = [{"value": random()} for _ in range(10000)]` with `UNWIND $numbers AS node`, and names "Submit a lot of single queries, one for each value" as the bad practice ([performance](https://neo4j.com/docs/python-manual/current/performance/), "Batch data creation"). The UNWIND page says each list element "is unwound and passed through `MERGE` to find or create the nodes and relationships" ([UNWIND](https://neo4j.com/docs/cypher-manual/5/clauses/unwind/), "Creating nodes from a list parameter").
- `graph/write.py` already does this: one query per label (nodes) or per (type, start label, end label) (edges), each row a map with `key`, `start`, `end`, `props` and the evidence lists.
- Nested maps inside a row are fine as parameters (the write path's `row.key.key` and `row.props` work today); they just cannot be stored as properties (Step 7 note, 4.1).

### 1.2 Batch size

- No primary source gives a recommended row count for driver-side UNWIND batches. The driver performance page uses 10,000 rows as its example and does not discuss size ([performance](https://neo4j.com/docs/python-manual/current/performance/)). `CALL { } IN TRANSACTIONS` defaults to 1,000 rows per inner transaction ([subqueries in transactions](https://neo4j.com/docs/cypher-manual/5/subqueries/subqueries-in-transactions/)), which is the only number the manuals give. Any "10k to 50k rows" rule of thumb is unconfirmed.
- Measured instead (ran): 10,000 `Organization` MERGEs and 15,000 `SUPPLIES` MERGEs, each edge with four 150-character spans in its lists, in one `execute_write`: 5.5 s, no memory error, default settings. Re-merging the same 15,000 edges as 15 transactions of 1,000 took 1.6 s (not a like-for-like comparison: the second run created nothing). The whole corpus is about this size, so batch size does not need tuning.

### 1.3 Labels and edge types

- Parameters cannot stand for labels, relationship types or property keys in static syntax (Step 7 note, 6.2, citing [parameters](https://neo4j.com/docs/cypher-manual/5/syntax/parameters/)). Since each UNWIND query has one label or one edge type, the loader groups rows by label or type in Python and builds one query per group from the allowlist, as `write.py` does with `graph.cypher.quoted()`.
- Dynamic labels on 5.26.31: the Step 7 note showed `MATCH (n:$($l))` scans every node. `UNWIND $rows AS row MERGE (n:$($l) {key: row.key})` plans `Merge` over `Filter` over `AllNodesScan`, while the static form plans `NodeUniqueIndexSeek(Locking)` (ran). MERGE with dynamic labels would also lose the index. Confirmed: keep static labels.

## 2. Transactions

### 2.1 Managed vs explicit

- `Session.execute_write(fn)`: "this function performs retries and ... the supplied transaction_function might get invoked more than once. Therefore, it needs to be idempotent" ([API 6.3](https://neo4j.com/docs/api/python-driver/6.3/api.html), `Session.execute_write`). It commits when the function returns and rolls back on an exception. The driver manual adds that the function may run several times but "the database queries inside it will always run only once" per attempt, and retries happen only when "the failure is deemed to be transient" ([transactions](https://neo4j.com/docs/python-manual/current/transactions/)).
- `max_transaction_retry_time` defaults to 30.0 s ([API 6.3](https://neo4j.com/docs/api/python-driver/6.3/api.html)).
- `Session.begin_transaction()` gives an unmanaged transaction "without the retries provided by managed transactions"; the caller commits or rolls back ([transactions](https://neo4j.com/docs/python-manual/current/transactions/); API 6.3).
- `Session.run()` is an auto-commit transaction. Since driver 6.2 it gets an "instant one-shot retry" for errors the server marks idempotent, which "encompasses only admission control errors"; `disable_auto_commit_retries` turns it off ([6.x changelog](https://github.com/neo4j/neo4j-python-driver/wiki/6.x-changelog), 6.2.0; API 6.3). The same API page elsewhere says auto-commit transactions "are not automatically retried on failure"; the two passages disagree, and the changelog supports the newer one.

### 2.2 What retry means for the write path

- A retry reruns the whole Python function in a new transaction after the old one rolled back. Database state is clean; only Python side effects repeat. `write_batch()`'s `_write` reads, checks and writes inside the function and keeps no state outside it, so it is safe to retry. `BatchRejected` and `IncompleteWrite` are Python exceptions, not driver errors, so they end the call without retry (the driver retries only driver-classified transient errors; not run for these two).
- The memory limit is the trap. `MemoryPoolOutOfMemoryError` is classified transient ([status codes](https://neo4j.com/docs/status-codes/5/errors/all-errors/)). With `db.memory.transaction.max=2MiB`, a 20,000-node `CREATE` inside `execute_query` was retried after 3.3 s, 9.3 s and 15.1 s waits before failing (ran). The same query would fail every time. At our scale it will not happen with default settings (section 2.3), but if a limit is ever set, a too-large batch costs 30 s before the error surfaces.

### 2.3 One transaction per filing, or for everything

- All three memory settings are dynamic and have no Enterprise marker on the settings page ([configuration settings](https://neo4j.com/docs/operations-manual/5/configuration/configuration-settings/)). The memory page: `dbms.memory.transaction.total.max` is "a global maximum memory usage for all of the transactions running on the server", `db.memory.transaction.total.max` "limits the transaction memory usage per database", `db.memory.transaction.max` "constrains each transaction", and "When any of the limits are reached, the transaction is terminated without affecting the overall health of the database" ([memory configuration](https://neo4j.com/docs/operations-manual/5/performance/memory-configuration/)).
- Values on 5.26.31 Community (ran): `db.memory.transaction.max` `0B`, `db.memory.transaction.total.max` `0B` (both meaning no limit, per the settings page), `dbms.memory.transaction.total.max` `686.00MiB`, `server.memory.pagecache.size` `512.00MiB`, heap sizes unset (computed from the container's resources). The settings page gives 70% of the heap as the global default, which puts the container's heap near 980 MiB (inferred, not read).
- Changing them: `CALL dbms.setConfigValue(...)` fails with `ProcedureNotFound` on Community (ran); the dynamic-settings page is marked Enterprise ([dynamic settings](https://neo4j.com/docs/operations-manual/5/configuration/dynamic-settings/)). Community reads them at start-up, from `neo4j.conf` or `NEO4J_db_memory_transaction_max`-style environment variables (naming rule in the Step 7 note, 6.4; `NEO4J_db_memory_transaction_max=2MiB` took effect, ran).
- Timeout: `db.transaction.timeout` defaults to `0s` (ran). A driver-side `unit_of_work(timeout=...)` bounds a load if wanted (Step 7 note, 6.5).
- Consequence: one transaction per filing is well inside the limits, and one for the whole corpus measured fine (section 1.2). The choice is about failure scope and simplicity, not memory. A whole-corpus transaction means a rejected record stops the entire load, with nothing written; per filing means a failed filing leaves the others in place.

### 2.4 `CALL { } IN TRANSACTIONS`

- The manual: it "is only allowed in implicit transactions"; inner batches default to 1,000 rows; `ON ERROR CONTINUE | BREAK | FAIL` since 5.7 (`FAIL` default), `RETRY` since 2025.03 (not on 5.26), `IN [n] CONCURRENT TRANSACTIONS` since 5.21; and "any inner transactions that were successfully committed remain unchanged and are not rolled back" ([subqueries in transactions](https://neo4j.com/docs/cypher-manual/5/subqueries/subqueries-in-transactions/)). The page does not say why it needs an implicit transaction; the plain reading is that it commits its own inner transactions, which an enclosing explicit transaction could not contain (unconfirmed as the stated reason).
- `Driver.execute_query` documents that "queries using CALL {} IN TRANSACTIONS or the older USING PERIODIC COMMIT will not work" and points to `Session.run()` ([API 6.3](https://neo4j.com/docs/api/python-driver/6.3/api.html)).
- On 5.26.31 (ran): inside `execute_write` and inside `begin_transaction()`, the query fails with `Neo.DatabaseError.Transaction.TransactionStartFailed`, "A query with 'CALL { ... } IN TRANSACTIONS' can only be executed in an implicit transaction". Through `session.run()` it succeeds.
- For the load this is the wrong tool: the write path's check-then-write must be one atomic transaction, and `IN TRANSACTIONS` commits early batches even when a later one fails. It suits bulk deletes (the Step 7 note's guarded wipe), not loads.

## 3. MERGE with uniqueness constraints

### 3.1 Nodes

- The MERGE page: constraints "provide index-backed performance improvements"; without a constraint, MERGE "only guarantees the existence of the pattern, not its uniqueness"; key constraints (Enterprise) are preferred because they also force existence ([MERGE](https://neo4j.com/docs/cypher-manual/5/clauses/merge/)). Community has only property uniqueness (Step 7 note, 2.1).
- On 5.26.31 with `CREATE CONSTRAINT ... REQUIRE n.key IS UNIQUE`, `UNWIND $rows AS row MERGE (n:Organization {key: row.key})` plans `NodeUniqueIndexSeek(Locking)` (ran). The `(Locking)` seek takes a lock on the key's index entry, which is what serializes concurrent MERGEs (inferred from the operator name; the lock mechanism is not described on the pages read).
- Race (ran): 8 threads × 20 auto-committed `MERGE (n:Organization {key:'Organization:intel'})` → 1 node. The same with a label that has no constraint → 8 nodes. Every label the loader MERGEs needs its Step 7 constraint applied first, which `apply_constraints()` already guarantees.
- MERGE on a property map matches on all properties in the map. MERGE on the key only, then `SET` the rest, as `write.py` does; putting `name` inside the MERGE pattern would create a second node when the name changes.
- `null` in a MERGE pattern fails: `MERGE (n:Organization {key: null})` gives `Neo.ClientError.Statement.SemanticError`, "Cannot merge the following node because of null property value for 'key'" (ran). `check_batch()` rejects missing keys before this point.
- `ON CREATE SET` / `ON MATCH SET` run conditionally on whether the pattern was created or matched ([MERGE](https://neo4j.com/docs/cypher-manual/5/clauses/merge/)).
- The driver performance page notes MERGE costs a match plus a create and suggests `CREATE` when the data is known to be new ([performance](https://neo4j.com/docs/python-manual/current/performance/)). A load into an empty graph could use `CREATE`, but MERGE keeps reloads idempotent, and the cost is negligible here.

### 3.2 Edges

- The MERGE page: when no matching relationship is found, Neo4j takes exclusive locks on both end nodes and matches again, which prevents "two transactions from creating the relationship simultaneously"; this does not depend on a constraint, since Cypher does not limit how many relationships of one type may join two nodes ([MERGE](https://neo4j.com/docs/cypher-manual/5/clauses/merge/), concurrency section).
- On 5.26.31 the write path's edge query plans `LockingMerge` over `Expand(Into)` (ran). Race (ran): 8 threads × 20 `MATCH (a) MATCH (b) MERGE (a)-[:SUPPLIES]->(b)` → 1 edge.
- So ADR-0004's "one edge per (start key, type, end key), plus `role`" holds through MERGE alone. A relationship uniqueness constraint (available on Community since 5.7) would need a stored edge ID, which the design does not have and does not need.
- Pitfalls:
  - Bind both ends with `MATCH` (or a prior `MERGE`) before `MERGE (a)-[r:T]->(b)`. A MERGE over a whole pattern with unbound nodes creates the whole pattern when any part is missing, duplicating nodes ([MERGE](https://neo4j.com/docs/cypher-manual/5/clauses/merge/), the `HAS_CHAUFFEUR` example).
  - An undirected `MERGE (a)-[r:T]-(b)` matches either direction and creates left to right ([MERGE](https://neo4j.com/docs/cypher-manual/5/clauses/merge/)). `COMPETES_WITH` is stored one way and queried both (CLAUDE.md, Step 7); writing it with a directed MERGE after a canonical ordering of the two keys in Python is deterministic, whereas an undirected MERGE would keep whichever direction came first.
  - Identity properties in the edge pattern (`{role: row.props.role}`) must be non-null: `MERGE (a)-[r:R {role: null}]->(b)` fails with a SemanticError (ran).
  - `MATCH` on a missing endpoint drops the row silently; `write.py` catches this with `count(*)` against `len(rows)` (`IncompleteWrite`). Keep that check. `dbms.cypher.lenient_create_relationship` is `false` (ran), which affects `CREATE`, not MATCH-then-MERGE.

## 4. Updating the parallel evidence lists

### 4.1 Replacing one chunk's entry

Step 8's rule: a chunk extracted again replaces its own entry. In Cypher, compute the indexes to keep from the stored `chunk_ids`, then rebuild each list from those indexes and append the new entries (ran on 5.26.31):

```cypher
UNWIND $rows AS row
MATCH (a:`Company` {`cik`: row.start.`cik`})
MATCH (b:`Organization` {`key`: row.end.`key`})
MERGE (a)-[r:`SUPPLIES`]->(b)
WITH r, row, coalesce(r.`chunk_ids`, []) AS old
WITH r, row, [i IN range(0, size(old) - 1) WHERE NOT old[i] IN row.`chunk_ids`] AS keep
SET r.`chunk_ids`       = [i IN keep | r.`chunk_ids`[i]]       + row.`chunk_ids`,
    r.`evidence_spans`  = [i IN keep | r.`evidence_spans`[i]]  + row.`evidence_spans`,
    r.`confidences`     = [i IN keep | r.`confidences`[i]]     + row.`confidences`,
    r.`extract_prompts` = [i IN keep | r.`extract_prompts`[i]] + row.`extract_prompts`
```

Stored `[c1, c2]` / `[s1, s2]`, then a row with `[c2, c3]` / `[s2-new, s3]`, gave `[c1, c2, c3]` / `[s1, s2-new, s3]` (ran). The four assignments are in one `SET` and all read the values from before the `SET`: `SET n.a = [9], n.b = n.a` stored the old `a` in `b` (ran). Not found stated in the manual pages read, so treat it as observed behaviour and compute `keep` in a `WITH` first, as above, so nothing depends on it.

### 4.2 Pitfalls

- **Two rows for the same edge in one statement lose an update.** With rows `[c4]` and `[c1]` for the same edge in one UNWIND, the result held `c1` but not `c4` (ran): the plan puts an `Eager` between computing `keep` and the `SET` (section 5), so both rows computed `keep` from the stored lists, and the second `SET` overwrote the first. The fix is in Python: fold all rows for one (start, type, end, role) into one row before the query. Whether the current append query in `write.py` loses entries the same way was not run; its `SET` reads `r.chunk_ids` again at write time, so it probably keeps both, but a test should pin it (unconfirmed).
- **Out-of-range index gives null.** `[1,2][5]` returns `null` (ran). If the lists ever went out of step, `r.evidence_spans[i]` would return `null`, and storing a list with a null fails with `Neo.ClientError.Statement.TypeError`, "Collections containing null values can not be stored in properties" (ran). The failure is loud, which is good, but `validate_graph()`'s length check is still what catches drift.
- **Types:** lists must be homogeneous; `[1, 2.5]` is stored as `LIST<FLOAT NOT NULL>` (ran), and `[]` loses its type (`LIST<NOTHING>`, Step 7 note 4.1). An edge whose last chunk is removed should be deleted, not left with empty lists (ADR-0004 consequences).
- **Setting a property to `null` removes it** (`SET n.x = null` left `keys(n)` empty, ran). A row map with a `null` value in `SET n += row.props` deletes that property, which is how an optional property such as `OWNS.stake` would vanish on a rewrite that lacks it.
- **Size:** no documented size limit for list properties was found on the pages read (unconfirmed). Evidence lists here hold at most 17 entries.
- **Single-valued properties** under the "most confident wins, ties to the latest" rule (e.g. `MetricValue.value`, `OWNS.stake`, a node's `name`) need the confidence of the evidence that set the current value. That is not stored per property, so in-Cypher resolution would have to re-derive it from the lists on every write. Doing it in Python over all of an entity's evidence is simpler and testable without a database.

### 4.3 Removing a filing's evidence

ADR-0004 gives Step 10 the job of removing a re-chunked filing's chunk IDs from edge lists. `MATCH ()-[r:SUPPLIES]->() WHERE any(x IN r.chunk_ids WHERE x IN $ids) ...` plans as `DirectedRelationshipTypeScan` + `Filter`, no Eager (ran), one query per edge type. At 13,000 edges a scan is cheap, as the ADR expected.

## 5. Eager operators

- The manual: Eager "causes all preceding operators to execute fully, for the whole dataset, before continuing execution", is added "to prevent updates from influencing pattern matching, or other read operations", and "can cause high memory usage when importing data"; to avoid it, split the work into simpler steps, for example "importing nodes and relationships separately" ([operators in detail](https://neo4j.com/docs/cypher-manual/5/planning-and-tuning/operators/operators-detail/#query-plan-eager)). `EXPLAIN` shows it with a reason in the operator's details.
- Plans on 5.26.31 (ran, `EXPLAIN` with the query text from `graph/write.py`, constraints in place):

| Query | Eager | Reason in plan |
|---|---|---|
| Node MERGE + `SET n += row.props` (write.py `_merge_nodes`) | yes | "read/set conflict for some property" |
| Node MERGE + named `SET n.name = ..., n.aliases = ...` | no | |
| Node MERGE + `ON CREATE SET n += row.props` | no | |
| Edge MERGE + `SET r += row.props` + append to `chunk_ids` (write.py `_edge_query`) | yes, 3 | "read/set conflict for property: chunk_ids" and "for some property" |
| Edge MERGE + `SET r += row.props` only | yes | "read/set conflict for some property" |
| Edge MERGE with `{role: ...}` + `SET r += row.props` | yes | "read/set conflict for some property" |
| Edge MERGE + named `SET`s from row values only | no | |
| Edge MERGE + in-Cypher replace (section 4.1) | yes | "read/set conflict for property: chunk_ids" |
| `EVIDENCED_BY` MERGE + `ON CREATE SET` (write.py) | no | |
| `EVIDENCED_BY` MERGE + plain `SET` of span and confidence | no | |
| Two node MERGEs of one label in one UNWIND | yes | "read/set conflict for label: Organization" |
| Node MERGE, then MATCH of the same label | yes | same |
| Node MERGE wrapped in `CALL (row) { ... }` | yes | "for some property" |

- What causes it here: `SET x += map` writes properties the planner cannot name, so it may conflict with the key the MERGE just read; reading a property after MERGE and writing it in the same statement; and two clauses touching the same label. `write.py` already loads nodes, edges and node evidence in separate queries, which avoids the label conflicts.
- Does it matter? At a few thousand rows per query the extra memory is small, and the 25,000-row single-transaction test ran in 5.5 s. The real cost is the lost update in section 4.2, which Eager's row-by-row isolation exposes. Removing Eager is cheap: generate one `SET n.\`prop\` = row.props.\`prop\`` per ontology property instead of `+=`, and compute the evidence lists in Python.
- Check plans in a test with `EXPLAIN` and `summary.plan` (the driver returns the plan tree as nested dicts with `operatorType` and `args.Details`; ran). Operator names carry a runtime suffix such as `Eager@neo4j`.

## 6. Community vs Enterprise, for this load

From the Step 7 note (section 2.1, all run on 5.26.31) plus this session:

| Feature | Community 5.26.31 |
|---|---|
| Node property uniqueness, single and composite | yes |
| Relationship property uniqueness (since 5.7) | yes |
| Existence, property type, node key, relationship key constraints | no (`ConstraintCreationFailed`) |
| Changing dynamic settings at runtime (`dbms.setConfigValue`) | no (`ProcedureNotFound`, ran) |
| Per-transaction and per-database memory limits | yes, set at start-up (ran with an env var) |
| `CALL { } IN TRANSACTIONS`, `ON ERROR`, `CONCURRENT TRANSACTIONS` | `IN TRANSACTIONS` yes (ran). `ON ERROR` (5.7) and `CONCURRENT` (5.21) are documented for these versions but not run; the page states no edition for `CONCURRENT` |
| More than one standard database | no |

`neo4j-admin database import` is the documented fastest path for a first load of large data ([performance](https://neo4j.com/docs/python-manual/current/performance/)), but it writes CSV into an offline, empty database and would bypass `write_batch()`'s checks, which invariant 4 relies on. Not suited here.

## 7. Recommendation for Step 10

1. **Fold in Python, write plainly.** Build the graph's final state in Python first: apply the alias map, fold all evidence for each node and edge across all filings with the Step 8 rules (a chunk replaces its own entry; most confident wins a single-valued property, ties to the latest, each conflict counted), order `COMPETES_WITH` ends canonically, and produce exactly one row per node key and per (start, type, end, role). The rules become pure functions with unit tests and no database. The Cypher then needs no list surgery: `MERGE` on the key, named `SET`s with the computed values.
2. **Rebuild rather than patch.** The full load measured in seconds, so Step 10 can wipe extracted data and reload from `data/extract/` and the alias map each time, instead of incrementally merging new evidence into stored lists. This also covers ADR-0004's "re-chunking a filing" case without the removal query in 4.3. (A grilling question: is a rebuild acceptable, or must loads be incremental?)
3. **Transactions:** keep `execute_write` (it is safe to retry, section 2.2). One transaction per filing, or one per label group for the whole corpus; both fit the defaults by a wide margin. Do not set `db.memory.transaction.max` unless needed, since its failures are retried for 30 s. Do not use `CALL { } IN TRANSACTIONS` for the load.
4. **Queries:** keep static, allowlisted labels in backticks (confirmed: dynamic-label MERGE scans all nodes). Replace `SET n += row.props` and `SET r += row.props` with one named `SET` per ontology property, which removes every Eager in the write path and stops a `null` in a row from deleting a property. Keep the `count(*)` vs `len(rows)` check. Add an `EXPLAIN` test that no write-path query plans `Eager`.
5. **Order:** constraints (already applied by `apply_constraints()`), then structural nodes, then extracted nodes, then edges, then `EVIDENCED_BY`, each as its own query, as `write.py` does now.

### Conflicts with the current design

- `graph/write.py` `_merge_nodes` and `_edge_query` use `SET n += row.props` / `SET r += row.props`. Both plan `Eager` on 5.26.31 (ran), and `+=` with a `null` value removes the property.
- `_edge_query` appends evidence and skips chunk IDs already listed; `_merge_node_evidence` keeps the first span with `ON CREATE SET`; `_merge_nodes` takes the latest batch's values. All three differ from Step 8's replace and most-confident rules. The docstring already says Step 10 replaces them; this note adds that doing so in Cypher hits the same-statement lost update (section 4.2), which is the reason to fold in Python.
- `check_batch()` rejects a chunk listed twice within one edge, but not the same edge appearing twice in one batch (only `DUPLICATE_CHUNK` exists in `graph/batch.py`). With replace semantics written in Cypher, that case loses evidence (ran, section 4.2). Either fold before writing or reject duplicate edge identities in `check_batch()`.
- Nothing found contradicts ADR-0004's edge identity, the per-filing RiskFactor and MetricValue nodes, the uniqueness constraints, or the choice of static labels.
