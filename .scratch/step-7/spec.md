# Step 7: Ontology as code

Status: done (2026-10-07; tickets 01–04 in PRs #12–#15, ticket 05 in its own PR)
Sources: RUNBOOK Step 7, docs/GRAPH-LAYER.md (Ontology), ADR-0004, docs/research/neo4j-driver-and-constraints.md, and the grilling session of 2026-10-07, whose decisions are in CLAUDE.md Decisions (Step 7 entries).

## Problem Statement

Steps 8 to 13 build a knowledge graph from the filings and compare graph retrieval with the vector arm. Nothing yet defines what the graph may contain. If the extractor in Step 8 runs before that exists, the model will name its own node and edge types, and the result is a graph nobody can query reliably. The RUNBOOK lists this as a known way the project goes wrong.

The project's credibility also rests on a promise the database can't keep alone: every extracted node and edge points at the chunk it came from (invariant 4). Neo4j Community has only uniqueness constraints. It can't require a property to exist, can't check a property's type, and can't hang an edge off another edge. A rule the database can't enforce has to live in code, be checked before every write, and be checkable again over the whole graph at any time.

The GRAPH-LAYER.md ontology was drafted before the eval set existed. The `dev` questions name things it has no label for: outside companies (TSMC, OpenAI, Altera), products (Blackwell, H20, MI308), regulations (export controls, the AI Diffusion Rule, Pillar Two), legal cases, deals and fabs.

## Solution

A new `graph` package holds the ontology as code: every node label, every edge type with the labels it may connect, and the required properties of each. Everything else is derived from that one module:
- the schema text that Step 8's extraction prompt will carry
- the hash that versions it
- the Neo4j constraints
- the checks the write path and `validate_graph()` run

Three operations sit on top of it:
- `apply_constraints()` creates the uniqueness constraints and proves they match the code.
- A guarded write path checks a whole batch against the ontology and evidence rules before any database call, and writes nothing if any item fails.
- `validate_graph()` reads the whole graph back and fails loudly on any node or edge that breaks a rule, however it got there. `python -m graph.validate` runs it from the command line.

The stop condition is met when the module exists, the constraints are applied to a live Neo4j, and `validate_graph()` is shown failing on each kind of broken graph.

## User Stories

### Ontology

1. As the researcher, I want every node label defined in one module, so that no other file can introduce a type.
2. As the researcher, I want every edge type defined with the labels it may connect, so that an edge such as `SUPPLIES` from a Period to a Chunk is impossible.
3. As the researcher, I want each label marked as structural or extracted, so that the evidence rule that applies to it is fixed by the ontology, not by the caller.
4. As the researcher, I want structural labels (Company, Filing, Period, Chunk) to cover only what EDGAR metadata gives, so that nothing read from text can pose as metadata.
5. As the researcher, I want Company to mean only the three filers, so that the graph never confuses NVIDIA, AMD or Intel with a firm they merely mention.
6. As the researcher, I want every other named firm or body to be an Organization, so that TSMC, OpenAI and Altera each get one node whatever role they play.
7. As the researcher, I want supplier, customer and competitor to be edges rather than labels, so that Intel can be both NVIDIA's competitor and the target of its investment without becoming two nodes.
8. As the researcher, I want a subsidiary to be an Organization with an `OWNS` edge, so that the same firm can be owned, partly sold or named as a competitor over time.
9. As the researcher, I want Product, Regulation, LegalProceeding, Agreement and Facility labels, so that the `dev` questions about products, export rules, legal cases, deals and fabs have somewhere to land.
10. As the researcher, I want Person and `HOLDS_ROLE_AT` with a required role, so that interim executives and board roles can both be represented.
11. As the researcher, I want RiskFactor to be one node per filing, so that Step 10 can link a risk factor to its counterpart a year later and show what changed.
12. As the researcher, I want MetricValue to be one node per filing, subject, metric and period, so that a restated figure appears next to the original instead of replacing it.
13. As the researcher, I want each MetricValue to carry its own period, so that the Q3 2023 comparison column in a Q3 2024 10-Q is recorded as Q3 2023.
14. As the researcher, I want the other extracted labels to be one node across all filings, so that "TSMC" is one node, not twenty-four.
15. As the researcher, I want Community left out until Step 12, so that the ontology holds only what can be built now.
16. As the researcher, I want each label's required properties listed in the module, so that a node missing its name is caught before it is written.
17. As the researcher, I want every node and edge to carry `ontology_version`, so that a graph built under an old ontology can be told apart from a new one.

### Schema text and versioning

18. As the researcher, I want the schema text for prompts generated from the module, so that the prompt and the code can never disagree.
19. As the researcher, I want the schema text to list each label with its required properties and a one-line description, so that the extractor knows what each label means.
20. As the researcher, I want each edge type listed with its allowed endpoints and a one-line description, so that the extractor can tell `SUPPLIES` from `CUSTOMER_OF`.
21. As the researcher, I want the descriptions written by hand in the module, so that I control the wording that steers extraction.
22. As the researcher, I want the schema text in a fixed order, so that the same ontology always gives the same text and hash.
23. As the researcher, I want an `ONTOLOGY_VERSION` integer and the SHA-256 of the schema text, so that a run can record exactly which ontology it used.
24. As the researcher, I want a test that fails when the schema text changes without a version bump, so that a silent ontology edit is impossible.

### Constraints

25. As the researcher, I want uniqueness constraints on `Company.cik`, `Filing.accession_no` and `Chunk.chunk_id`, so that a structural node can't be loaded twice.
26. As the researcher, I want a composite uniqueness constraint on Period's company and fiscal period, so that NVIDIA's FY2026 and AMD's FY2026, which cover different dates, stay separate.
27. As the researcher, I want a uniqueness constraint on the `key` of every extracted label, so that entity resolution in Step 9 has a stable identity to merge on.
28. As the researcher, I want constraints applied with one idempotent call, so that running it twice changes nothing.
29. As the researcher, I want the call to read the constraints back and compare them with the code, so that a constraint Neo4j kept under an old definition is caught. `IF NOT EXISTS` silently keeps an old constraint with the same name.
30. As the researcher, I want an extra constraint on an ontology label to fail the call, so that a leftover from an older version can't change write behaviour unnoticed.
31. As the researcher, I want the call to create a single `:GraphMeta` node holding the version and hash, so that the database records which ontology it was built under.
32. As the researcher, I want the call to refuse when `:GraphMeta` holds another version, so that moving a graph to a new ontology is an explicit step (Step 10), never an accident.

### Write path

33. As the researcher, I want one function that writes a batch of nodes and edges, so that there is exactly one way into the graph.
34. As the researcher, I want the whole batch checked in Python before any database call, so that a bad item can't leave a half-written batch.
35. As the researcher, I want a batch with any violation to write nothing and list every violation, so that I fix all problems in one pass.
36. As the researcher, I want an extracted edge without chunk IDs rejected, so that invariant 4 holds at the door.
37. As the researcher, I want an extracted edge whose spans don't match its chunk IDs in number rejected, so that every chunk is paired with its quote.
38. As the researcher, I want an extracted node without at least one evidence chunk rejected, so that no extracted node exists without evidence.
39. As the researcher, I want an edge whose endpoint labels aren't allowed for its type rejected, so that the graph stays queryable.
40. As the researcher, I want an unknown label or edge type rejected, so that the write path can't be used to extend the ontology.
41. As the researcher, I want a missing required property or wrong property type rejected, so that Community's lack of existence and type constraints doesn't matter.
42. As the researcher, I want an edge that references a chunk not in the graph or the batch rejected, so that evidence never dangles.
43. As the researcher, I want a valid batch written in one transaction, so that it is all there or none of it is.
44. As the researcher, I want writing an existing edge again to add its new chunks and spans to the lists, so that one supplier relation seen in ten chunks is one edge with ten pieces of evidence.
45. As the researcher, I want a chunk already listed on an edge not to be added twice, so that rewriting a batch doesn't inflate the evidence.
46. As the researcher, I want edge identity to be start, type and end, plus role where the type has one, so that NVIDIA's CEO and CFO roles held by one person stay two edges.
47. As the researcher, I want node evidence to accumulate the same way through `EVIDENCED_BY` edges, so that nodes and edges follow one rule.
48. As the researcher, I want labels and edge types placed in Cypher only from the ontology's allowlist, quoted in backticks, so that no text from a filing or a model can inject Cypher.
49. As the researcher, I want every value passed as a query parameter, so that property values can't inject Cypher either.

### validate_graph()

50. As the researcher, I want one function that checks the whole graph, so that I can prove the graph is clean after any load.
51. As the researcher, I want it to find nodes with no ontology label or more than one, so that a stray label is caught.
52. As the researcher, I want it to find edge types outside the ontology, so that a write that went around the write path is caught.
53. As the researcher, I want it to find edges between labels their type doesn't allow, so that a malformed edge is caught.
54. As the researcher, I want it to find missing required properties and wrong types, so that the rules Community can't enforce are still checked.
55. As the researcher, I want it to find extracted nodes with no `EVIDENCED_BY` edge to a Chunk, so that an unevidenced node fails loudly.
56. As the researcher, I want it to find extracted edges with empty chunk lists, chunk IDs with no Chunk node, or span lists of the wrong length, so that an unevidenced edge fails loudly.
57. As the researcher, I want it to check the structural shape (one `FILED` and one `COVERS_PERIOD` per Filing, one `PART_OF` per Chunk), so that a filing with no company or a chunk with no filing is caught.
58. As the researcher, I want it to check `:GraphMeta` against the code's version and hash, so that a graph from another ontology version fails.
59. As the researcher, I want it to check, when Postgres is available, that each Chunk node's ID exists in Postgres under the same filing, so that graph evidence always resolves to real filing text.
60. As the researcher, I want it to collect every violation before failing, so that one run shows the full damage.
61. As the researcher, I want the error to give a count per check and up to 20 examples each, so that I can find the bad items without the output flooding.
62. As the researcher, I want an empty graph with a correct `:GraphMeta` to pass, so that a fresh database is valid.
63. As the researcher, I want `python -m graph.validate` to run it with distinct exit codes, so that a script or a future CI job can tell "invalid" from "couldn't run".
64. As the researcher, I want the command to say which checks ran and whether the Postgres check was skipped, so that a pass is never mistaken for more than it proved.

### Tests and environment

65. As the researcher, I want a separate Neo4j test instance locally, so that tests never touch the graph I'm building.
66. As the researcher, I want graph tests to refuse to run against a database without a test marker node, so that a misconfigured URL can't wipe real data.
67. As the researcher, I want a pinned Neo4j service in CI, so that constraints and validation are tested against the real database on every PR.
68. As the researcher, I want graph tests to skip without the test database locally, and a guard test that fails in CI if it's missing, so that CI can't pass by skipping them.
69. As the researcher, I want test graphs built around real chunk IDs from the fixture filings and quoting real spans, so that the project's real-fixtures rule holds.
70. As the researcher, I want each broken-graph test to break exactly one thing in an otherwise valid graph, so that each check is proven on its own.
71. As the researcher, I want the stop condition's one-off run against my local Neo4j recorded in BUILD-LOG, so that the constraints are shown applied outside the tests.

## Implementation Decisions

### The ontology module
- A new `graph` package, separate from the empty `index` package, which is left alone.
- The ontology is plain frozen data: a set of label definitions and a set of edge-type definitions, with no database code. Each label definition has:
  - its name
  - its kind (structural or extracted)
  - its key properties
  - its required properties with their types
  - a one-line description
- Each edge-type definition has its name, its kind, the start labels and end labels it allows, its required properties (`role` for `INVOLVED_IN` and `HOLDS_ROLE_AT`, optional `stake` for `OWNS`) and a one-line description.
- Labels:
  - structural: Company, Filing, Period, Chunk
  - extracted: Organization, Segment, Product, RiskFactor, Regulation, LegalProceeding, Agreement, Facility, Person, MetricValue
- Edge types and endpoints are exactly the list in CLAUDE.md Decisions (Step 7). `FILED`, `COVERS_PERIOD`, `PART_OF` and `EVIDENCED_BY` are structural; the rest are extracted.
- Required properties:
  - Company: cik, name, ticker
  - Filing: accession_no, form_type, filing_date (the Postgres column name; user decision 2026-10-07), fiscal_period
  - Period: cik, fiscal_period
  - Chunk: chunk_id, accession_no, section (never text)
  - extracted labels: key and name, except RiskFactor (key, title) and MetricValue (key, concept, value, unit, period)
  - every node and edge: `ontology_version`
- How an extracted node's `key` is built is not decided here. Steps 8–9 own it. Step 7 only requires that it exists, is a string and is unique per label.
- Evidence (ADR-0004):
  - Every extracted edge stores `chunk_ids` (a non-empty list of strings) and `evidence_spans` (a list of strings of the same length).
  - Every `EVIDENCED_BY` edge stores `evidence_span`.
  - Structural nodes and edges carry no chunk evidence. Their evidence is the filing record.
- `PERSISTS_AS` is defined now with both ends RiskFactor. Its evidence is the chunks of both risk factors. Step 10 builds it.
- `COMPETES_WITH` is stored in one direction. The module says it is symmetric, so traversal queries can ignore direction.

### Schema text and version
- A function produces the schema text from the module: labels in a fixed order with properties and descriptions, then edge types with endpoints and descriptions.
- `ONTOLOGY_VERSION` starts at 1. The module exposes the SHA-256 of the schema text.
- A test pins the expected hash for the current version, so that any edit to labels, edge types, properties or descriptions fails until the version is bumped and the pin updated together.

### Batch checking
- A pure function takes a batch (nodes and edges as plain typed records) and returns every violation. Its rules: the label or type is known, the endpoints are allowed, required properties are present with the right types, evidence is present and shaped correctly, and every chunk ID an edge cites is a Chunk in the batch or named as already in the graph. The caller passes the set of existing chunk IDs.
- A violation names the item, the rule and a readable message.
- Added in ticket 03 (2026-10-07), each serving stories 40–42 and 47 or invariant 4:
  - a property outside the ontology is rejected, so the write path can't add one;
  - `ontology_version` comes from the write path, and a caller-supplied one is rejected;
  - an extracted node carries its evidence on the node record as (chunk ID, span) pairs, which the write path turns into `EVIDENCED_BY` edges; an `EVIDENCED_BY` edge passed as an edge is rejected, and so is evidence on a structural node;
  - a chunk ID listed twice on one item, or a blank span, is rejected;
  - an edge endpoint must be a node in the batch or already in the graph, so a MATCH can't silently drop the edge. The caller passes the existing endpoint nodes alongside the existing chunk IDs.

### Constraints
- `apply_constraints(driver)`:
  1. Creates each uniqueness constraint with a fixed name and `IF NOT EXISTS`.
  2. Reads `SHOW CONSTRAINTS` and compares type, label and properties with the code.
  3. Raises `ConstraintMismatch` listing what is missing, different or extra on an ontology label.
  4. Then creates `:GraphMeta` if absent, or raises if it holds another version or hash.
- Constraint names come from the module, so the code and the database agree on them.
- Only uniqueness constraints are used. Existence, type and key constraints are Enterprise-only (research note, section 2.1).

### Write path
- `write_batch(driver, batch)`:
  1. Runs the batch check, asking the graph for the existing chunk IDs the batch cites.
  2. Raises with the full violation list if there are any, before any write.
  3. Otherwise writes the batch in one write transaction.
- Nodes are merged on their key properties. Edges are merged on (start key, type, end key, role where present). On a merge, new chunk IDs are appended with their spans, and chunk IDs already listed are skipped.
- Added in ticket 03 (2026-10-07): `write_batch` refuses, inside its transaction, a graph with no `:GraphMeta` or one built under another ontology, so a batch never lands before `apply_constraints()` has run. On a rewrite, a node's or edge's other properties take the batch's values, and an `EVIDENCED_BY` edge keeps the span it was first written with (user decision 2026-10-07; revisit in Step 8 with `confidence`).
- Labels and edge types are interpolated only from the ontology's definitions, in backticks. Values are always parameters. Dynamic-label syntax (`$(...)`) is not used, because on 5.26 a MATCH with it scans every node (research note, section 6.3).
- The driver is created with `telemetry_disabled=True`, and every query passes the database name explicitly. Connection settings come from environment variables: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`.

### validate_graph()
- `validate_graph(driver, postgres=None)` runs the checks in user stories 51–59 with read queries and returns a report of counts per check plus up to 20 examples each. If any check found a violation, it raises `GraphValidationError` carrying that report.
- The Postgres check runs when a connection is given. It uses parameterised queries against the existing `chunks` table.
- `python -m graph.validate`:
  - Builds the driver from the environment, and uses Postgres when `DATABASE_URL` is set.
  - Prints the report, including whether the Postgres check ran.
  - Exit codes follow `eval.gate`: 0 valid, 2 bad input, 3 could not run (missing settings, Neo4j unreachable), 4 violations found.

### Environment and dependencies
- New dependency `neo4j==6.3.1`, approved in the grilling. It brings only `pytz`.
- docker-compose gains a `neo4j-test` service from the same pinned image, on 127.0.0.1:7688 (bolt) with its own volume and credentials from environment variables.
- Graph tests connect through `NEO4J_TEST_URI`, `NEO4J_TEST_USER` and `NEO4J_TEST_PASSWORD`. This keeps the grilling's intent ("skip without the test database; never touch the real graph") while letting `.env` hold both URLs. `.env.example` gains these three.
- Test marker:
  - The test fixture creates a marker node only when the test database is completely empty.
  - It refuses to run, failing rather than skipping, when the database has data but no marker.
  - It wipes everything except the marker between tests.
- CI's `lint-and-test` job gains a Neo4j service from the same pinned image, with auth from job env, a health check, and the `NEO4J_TEST_*` variables. The `quality-gate` job is unchanged.

## Testing Decisions

- Good tests here check behaviour at the two seams the user approved, never internal helpers:
  - the pure batch check and schema text, with no database
  - the live-graph operations (`apply_constraints`, `write_batch`, `validate_graph`) against the Neo4j test instance
  - the command, through `main(argv)`, for exit codes and output
- Deterministic rules are written test-first: each test is shown failing before the code that passes it.
- Pure tests:
  - each label and edge-type rule, with one violation per test
  - the schema text's order and pinned hash
  - every edge type's endpoint list matches CLAUDE.md Decisions
  - batch violations are all reported, not just the first
- Live tests:
  - constraints appear in `SHOW CONSTRAINTS`
  - rerunning `apply_constraints` is a no-op
  - a constraint created under a known name with a different property raises `ConstraintMismatch`
  - an extra constraint on an ontology label raises
  - a `:GraphMeta` with another version is refused
  - a rejected batch leaves the database unchanged
  - writing the same edge twice yields one edge with merged, de-duplicated evidence
  - a role edge with two roles stays two edges
  - a duplicate key violates the uniqueness constraint
  - `validate_graph()` passes on the valid test graph and on an empty graph
- Each `validate_graph()` failure case starts from the valid test graph and breaks one thing with raw Cypher that goes around the write path:
  - an unknown label
  - an unknown edge type
  - a disallowed endpoint
  - a missing property
  - a wrong property type
  - an extracted node with no evidence
  - an empty `chunk_ids` list
  - a dangling chunk ID
  - span and chunk lists of different lengths
  - a Filing without `FILED`
  - a Chunk without `PART_OF`
  - a wrong `:GraphMeta`
  - a Chunk node unknown to Postgres, using the existing throwaway-schema Postgres fixture
- Test data: Chunk nodes use real chunk IDs from running the chunker on the gzipped fixture filings. Extracted nodes and edges are hand-written, but every span is checked by the test helper to be a substring of its chunk's text, so a bad quote fails the fixture.
- Prior art:
  - `test_eval_schema.py` and `test_question_filter.py` for pure rule tests
  - `test_store.py` and the throwaway-schema fixture in `tests/conftest.py` for skip-without-database plus a CI guard
  - `test_gate.py` for command exit codes
- CI keeps its no-network rule. Neo4j on localhost is allowed by pytest-socket's localhost allowance.

## Out of Scope

- Extraction, the extract prompt and its JSON Schema (Step 8). The schema text is ready for it.
- How extracted keys are built, normalization and entity resolution (Steps 8–9).
- The loader that writes structural nodes from Postgres, `PERSISTS_AS` linking and moving a graph between ontology versions (Step 10).
- Checking that evidence spans appear verbatim in their chunks against Postgres (Step 8, OPEN-DECISIONS).
- `confidence` and the extract prompt version on edges (Step 8, with a version bump).
- The Community label and community detection (Step 12).
- Making `validate_graph()` a CI gate (Step 13, once a real graph exists).
- Read-only guards for text-to-Cypher (Step 11, OPEN-DECISIONS).
- Any change to the vector arm, the eval harness or the quality gate.

## Further Notes

- Instructions from chat: the workflow gained a `/research` step (CLAUDE.md workflow step 2). This step's research is docs/research/neo4j-driver-and-constraints.md.
- The `dev` questions informed which labels exist. `test` questions were not read. Choosing the ontology from `dev` is tuning on `dev`, which the research plan allows.
- One cost from ADR-0004: finding the edges that cite a given chunk scans every edge of that type, because no Neo4j index matches one element of a list. At 24 filings this is cheap.
- One PR per ticket, as in Step 6. `main` requires `lint-and-test`, `quality-gate` and a pull request. Adding the Neo4j service to CI goes in the first ticket that needs it.
