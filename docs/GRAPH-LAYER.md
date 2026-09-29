# FilingIntel v2 — Graph Retrieval Layer

> Design spec for the graph layer. Read with `CLAUDE.md` and `docs/RUNBOOK.md`. It assumes v1 (vector RAG + Ragas/DeepEval harness + CI quality gate, RUNBOOK steps 1–6) is built and green. It adds a knowledge-graph retrieval path alongside the vector path and uses the existing eval harness to measure which one wins, on which kinds of question. Build order and stop conditions live in RUNBOOK steps 7–13; this file holds the design detail.

---

## The thesis

Vector retrieval finds passages that *look like* the question. It has no representation of how facts connect. Two failure modes follow:

1. **Multi-hop questions.** "Which risk factors did NVDA newly disclose this year, and which reporting segments do they touch?" requires joining three things — a risk factor, a period-over-period diff, and a segment mapping — that never co-occur in one chunk. Vector search returns three plausible chunks and the model fabricates the join.
2. **Global questions.** "What themes run across all three companies' supply-chain disclosures?" requires reasoning over the corpus, not retrieving from it. Top-k retrieval structurally cannot answer this: the answer isn't in any k chunks.

A knowledge graph makes the relationships explicit and queryable, so the model looks them up instead of inferring them.

**This is a hypothesis, not a conclusion.** The deliverable is not "we built GraphRAG." The deliverable is a measured, published comparison of vector and graph retrieval over one corpus and one eval set, with the honest result — including where graph *loses*. It will likely lose on simple single-fact lookup, at higher latency and higher indexing cost. Say so.

---

## What changes from v1

| | v1 | v2 |
|---|---|---|
| Retrieval | vector top-k with metadata filter | router → vector / graph_local / graph_traversal / graph_global |
| Index | embeddings only | embeddings + property graph + community summaries |
| Eval | one arm, gated in CI | four arms plus router, compared per query class, gated in CI |
| Citations | chunk-level | chunk-level **and** path-level (node + edge provenance) |

Everything in v1 stays. The vector path is the control arm — do not delete it or degrade it.

---

## Ontology

Design the schema by hand. Do not let the model invent node types at extraction time — that is the single biggest reason hobby GraphRAG projects produce unqueryable graphs.

### Nodes

| Label | Key properties |
|---|---|
| `Company` | `cik` (canonical key), `name`, `ticker`, `sic` |
| `Filing` | `accession_no`, `form_type` (10-K/10-Q), `filed_date`, `fiscal_period` |
| `Period` | `fiscal_period`, `start_date`, `end_date` |
| `Segment` | `name`, `normalized_name` |
| `RiskFactor` | `title`, `category`, `first_seen_period` |
| `Person` | `name`, `role` |
| `Subsidiary` | `name`, `jurisdiction` |
| `MetricValue` | `concept` (XBRL tag), `value`, `unit`, `period` |
| `Chunk` | `chunk_id`, `text`, `section`, `char_span` |
| `Community` | `level`, `summary`, `member_count` |

### Edges

```
(Company)-[:FILED]->(Filing)
(Filing)-[:COVERS_PERIOD]->(Period)
(Filing)-[:DISCLOSES]->(RiskFactor)
(Filing)-[:REPORTS]->(MetricValue)
(Company)-[:HAS_SEGMENT]->(Segment)
(RiskFactor)-[:AFFECTS]->(Segment)
(RiskFactor)-[:PERSISTS_AS]->(RiskFactor)   // period-over-period linkage
(Company)-[:OWNS]->(Subsidiary)
(Person)-[:OFFICER_OF]->(Company)
(:*)-[:EVIDENCED_BY]->(Chunk)               // MANDATORY on every extracted node/edge
(Chunk)-[:PART_OF]->(Filing)
```

### The provenance rule (non-negotiable)

**Every extracted node and every extracted edge carries at least one `EVIDENCED_BY` pointer to a `Chunk`, and every `Chunk` carries a character span into the source filing.** An edge with no evidence is a hallucination that has been laundered into a database and will be trusted forever after. Enforce this as a constraint at write time, not as a lint. This is the thing that makes v2 defensible in a regulated context and it's what separates it from every GraphRAG demo repo.

---

## Indexing pipeline

```
EDGAR filing
  ↓ (v1, unchanged) section-aware chunking
Chunk
  ↓ [PROMPT 1] entity + relation extraction, per chunk, schema-constrained
Candidate triples (with evidence spans + confidence)
  ↓ [PROMPT 2] entity resolution / normalization
  ↓           + deterministic pre-pass (see below)
Canonical entities
  ↓ constraint-checked write
Property graph (Neo4j)
  ↓ Leiden community detection at 2 levels (Python)
Communities
  ↓ [PROMPT 3] community summarization
Community reports → embedded for global search
```

### Entity resolution — do the deterministic part first

Do **not** hand "is `NVIDIA Corp` the same as `NVDA`?" to an LLM when a lookup table answers it. Order of operations:

1. **Deterministic:** CIK and ticker are authoritative for `Company`. XBRL concept tags are authoritative for `MetricValue`. Exact-match after case/whitespace/suffix normalization (`Inc.`, `Corp`, `Ltd`).
2. **Blocking:** candidate pairs only within the same label and above an embedding-similarity threshold. Never all-pairs.
3. **LLM adjudication** on the surviving ambiguous pairs only, with Prompt 2, and it must return evidence.
4. **Merge log:** every merge is written to an append-only log with the deciding evidence, and every merge is reversible.

Report your entity-resolution precision/recall on a hand-labelled sample of ~100 pairs. That number belongs in the README. Almost nobody publishes it, and it's the number an experienced interviewer will ask for.

---

## Retrieval

### Router

A LangGraph node classifies each query into one of four classes, then dispatches:

| Class | Example | Arm |
|---|---|---|
| `lookup` | "What was NVDA's Q3 data-center revenue?" | `vector` (+ direct XBRL) |
| `local` | "What did NVDA say about export controls?" | `graph_local`: anchor entity → 1–2 hop neighbourhood → attached chunks |
| `multi_hop` | "Which new risk factors touch the Gaming segment?" | `graph_traversal` (text-to-Cypher) |
| `global` | "What supply-chain themes recur across all three companies?" | `graph_global`: community reports → map-reduce |

Route with a cheap classifier and **log the routing decision** as a first-class span in Phoenix. Router accuracy is itself an eval metric — a graph system that routes badly performs worse than plain vector RAG, and that's a finding worth reporting.

### Text-to-Cypher hardening

Raw text-to-Cypher is fragile. Required guards:
- Inject the live schema (labels, rel types, properties) into the prompt — never a hardcoded copy that drifts.
- Validate the generated query against the schema before execution; reject invented labels.
- Read-only credentials. Statement timeout. `LIMIT` enforced by the executor, not requested in the prompt.
- On validation failure: one repair attempt with the error message, then fall back to `graph_local`. Log the fallback.
- **Track and report Cypher validity rate.** Expect well under 100% first-pass on a non-trivial schema. Publishing the real number is more impressive than pretending it's perfect.

### Global search

Map-reduce over community reports: score each community report for relevance to the query, generate partial answers from the top-scoring ones, reduce to a final answer. Every claim in the final answer must trace back through `Community → member entities → EVIDENCED_BY → Chunk`. Cost-cap the fan-out; log tokens per query.

---

## Evaluation — the actual deliverable

The eval set is the ~120 hand-written, class-labelled questions from RUNBOOK step 4, each with a gold answer and gold source chunks. It is human-authored; no agent writes or edits it.

Run every question through all four arms — `vector`, `graph_local`, `graph_traversal`, `graph_global` — and through the router.

### Metrics per (arm × class)

- Ragas: context precision, context recall, faithfulness, answer relevancy
- **Path precision** (new): fraction of retrieved graph edges that are actually load-bearing for the gold answer. This metric does not exist off the shelf — write it. It's also your Ragas OSS contribution.
- Citation validity: do cited spans exist and support the claim
- p50/p95 latency
- Cost per query (indexing amortized + query-time), reported separately

### Output

A committed `benchmarks/results.md` with the arm × class matrix, regenerated by CI, plus a short written interpretation. The headline of the project becomes a sentence of the form: *"graph traversal changed context recall on multi-hop questions by X% at Y× latency and Z× indexing cost; on single-fact lookup it was [better/worse] than vector; routing captured [some/most/none] of the gain."* Fill it in only from real runs.

### CI gate

Extend the v1 gate: block merge if any arm regresses beyond threshold on its own class, **and** if the provenance constraint is violated anywhere in the graph, **and** if Cypher validity rate drops below its floor.

---

## Prompts

Five stages. Keep them in `prompts/` as versioned files, not inline strings — the eval harness needs to attribute score changes to prompt versions.

1. **Extract** — schema-constrained. Supply the allowed labels and relation types explicitly. Require `evidence_span` and `confidence` on every triple. Instruct: emit nothing not stated in the chunk; forward-looking statements are `DISCLOSES`, never `REPORTS`.
2. **Resolve** — adjudicate ambiguous entity pairs only. Return `same` / `related` / `distinct` with evidence. Never merge without evidence.
3. **Cypher** — schema injected, read-only, no invented labels, return query + reasoning.
4. **Answer** — answer only from retrieved paths; cite the node and edge path for each conclusion; state uncertainty; **do not infer causation from co-occurrence in the graph** (an edge means "these were mentioned together in a filing," not "this caused that" — critical in a financial context).
5. **Maintain** — on a new filing, classify each incoming fact as new / duplicate / contradiction / update / uncertain. Contradictions are flagged for review, never silently overwritten. Filings are restated; this matters.

---

## Stack additions

- **Graph store:** Neo4j Community via Docker (decided; see CLAUDE.md). Kùzu was considered and rejected because its upstream was archived in October 2025.
- **Community detection:** Leiden in Python via `leidenalg` or `graspologic`, independent of the graph database.
- **Orchestration:** LangGraph for the router.
- **Tracing:** Phoenix.
- **Reference:** read Microsoft's `graphrag` repo architecture docs before starting — then build your own thinner version. Do not vendor their pipeline; the point is that you can build it.

Everything else from v1 unchanged.

---

## Milestones

These map onto RUNBOOK steps. The RUNBOOK stop conditions govern; the acceptance checks here are additional.

**G1 — Schema and extraction (steps 7–8).** Ontology defined as code with constraints. Extraction runs over one company's 10-K. *Accept:* zero constraint violations, 100% of nodes/edges have evidence pointers.

**G2 — Entity resolution (step 9).** Deterministic pass + blocking + LLM adjudication + reversible merge log. *Accept:* ER precision/recall measured on a 100-pair labelled sample and written to the README.

**G3 — Load, local + traversal retrieval (steps 10–11).** Guarded load, `PERSISTS_AS`, text-to-Cypher with validation and fallback. *Accept:* Cypher validity rate reported; multi_hop eval subset runs end to end.

**G4 — Communities + global search (step 12).** Leiden at two levels, community reports, map-reduce answering. *Accept:* global-class questions answered with traceable provenance; cost per global query measured and capped.

**G5 — Router and four-arm benchmark (step 13).** Router with decisions traced in Phoenix, full matrix, `benchmarks/results.md` generated in CI, gate extended. *Accept:* results committed, written interpretation includes every place where graph lost.

---

## Non-goals

- No buy/hold/sell signals, price targets, or recommendations. Unchanged from v1.
- No causal claims. The graph encodes disclosure relationships, not causation.
- No corpus expansion. Same three tickers (NVDA, AMD, INTC). Graph quality over graph size — a large sloppy graph is worse than no graph.
- No auto-schema-discovery. Hand-designed ontology only.
- Do not delete or weaken the v1 vector path. It is the control.

---

## For the coding agent

- Build G1–G5 in order. Do not start G3 before G2's merge log exists.
- The provenance constraint is a hard constraint, enforced at write time. If a write would create an unevidenced node or edge, fail loudly.
- Never let the extraction prompt introduce a label or relation type outside the declared ontology. Validate before write.
- The graph is read-only at query time. Query credentials cannot write.
- Every retrieval arm must be independently runnable from the CLI so the benchmark can invoke them uniformly.
- When a metric moves, the harness must be able to attribute it to a prompt version, a schema version, or a code change. Version all three.
- Prefer failing a query loudly over answering it from a path you can't cite.