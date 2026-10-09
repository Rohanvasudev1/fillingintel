# FilingIntel — build order

A sequenced runbook. Each step has a **stop condition** — do not start the next step until it's met. The ordering is deliberate: the measurement layer exists before the thing being measured.

Rough shape: steps 1–6 are v1 (vector + eval + CI). Steps 7–13 are v2 (graph). Step 14 is the write-up, and it is not optional.

Stack decisions are recorded in `CLAUDE.md` under Decisions and take precedence over anything older in this file.

---

## Phase A — foundation

### Step 1. Repo and environment

```
filingintel/
  CLAUDE.md      lean project instructions (loaded every session)
  docs/          ARCHITECTURE.md, RUNBOOK.md, GRAPH-LAYER.md, design/
  ingest/        EDGAR download + parsing
  index/         chunking, embedding, graph build
  retrieve/      the four arms
  eval/          eval set, metrics, runner
  api/           FastAPI
  ui/            Streamlit
  prompts/       versioned prompt files
  benchmarks/    generated results
  data/          gitignored
```

Python 3.11+, `uv` or Poetry. Pin everything. CLAUDE.md is the lean root file; specs live in docs/.

Set up `pytest`, `ruff`, and a GitHub Actions workflow that runs both on push. Add a `docker-compose.yml` for Postgres (with pgvector) and Neo4j. Do this now, while the repo is empty and it takes ten minutes.

**Stop condition:** empty repo, green CI, one trivial passing test.

### Step 2. Ingest EDGAR

Three companies in one sector so their language is comparable: NVDA, AMD, INTC. Two fiscal years each, 10-K plus the 10-Qs. That's roughly 24 filings. Resist expanding this.

Practical notes that will save you an afternoon:
- EDGAR requires a `User-Agent` header with a real name and email. Requests without it get 403.
- Rate limit is 10 requests/second. Add a sleep; don't get your IP blocked.
- Start from `https://data.sec.gov/submissions/CIK##########.json` to enumerate a company's filings, then fetch documents from the archives path.
- Store the raw HTML unmodified in `data/raw/`, keyed by accession number. Never re-download during development.

Parse to clean text with section boundaries preserved. Item 1A (risk factors), Item 7 (MD&A), and Item 8 (financials) are the sections that matter. `sec-parser` or `edgartools` will get you most of the way; expect to write custom handling for at least one company's formatting.

**Stop condition:** 24 filings on disk as raw HTML plus parsed text with section labels. Spot-check three by eye — parsed section boundaries are wrong more often than you'd think.

#### Step 2b — acceptance checks (amended 2026-10-02)

The parser is accepted only when all of these hold. `/step-review` checks them (it replaced `/code-review` on 2026-10-03).

1. **Full corpus.** Run over every filing in the manifest (two most recent complete fiscal years per ticker: each 10-K plus its three 10-Qs, aligned by `fiscal_period`; 24 filings), downloading any not cached. `python -m ingest.corpus` writes `spikes/corpus_report.txt` with the commit and config.
2. **Report per filing:** required sections present (yes/no), extraction method per required section (`edgartools` / `heading` / `cross_reference_index`) and character length of each. The parser also emits `preamble` (the leading text) and `continuation` (a later span of an item); a section's reported method is its first span's, and its length is the sum of its spans. Flag any section under 2,000 characters.
3. **Failure policy.** A required section that is missing, under 2,000 characters, or not starting at its own title is a bug, fixed test-first. The only accepted short section is an Item 8 that points to Item 15 (NVIDIA), verified in the filing and tested. Never weaken a check to pass.
4. **Position rules generalise.** The 90%, last-20% and 15% rules were tuned on 6 filings; the corpus run is the evidence they hold.
5. **Boundaries verified against the filing.** Check a section's end as well as its start (INTC 10-K Item 7: pin the real last sentence of MD&A in a test).
6. **Whole-filing coverage.** Text before the first located section is kept as a `preamble` section. The sections' text reproduces the filing's markdown (whitespace aside).
7. **Tables.** Report detected vs inserted into section text, per filing. (Superseded in Step 3a: tables are located by position in the text and reconciled exactly with edgartools' table list; see the corpus report.)
8. **Pins and fixtures.** edgartools pinned `>=5.59,<6.0`. Fixtures are real filings (gzipped), rebuilt from `data/raw/` by `scripts/build_fixtures.py`.
9. **Process.** Tests written first and shown failing; python-review, verification-loop and `/step-review` run; BUILD-LOG entry ending "Next session starts with"; commit pushed and CI green.

### Step 3. Chunking

Section-aware, not naive. A chunk should not span an Item boundary. Target ~800 tokens with ~100 token overlap, but keep tables intact even when that means an oversized chunk.

Every chunk carries: `chunk_id`, `cik`, `accession_no`, `form_type`, `fiscal_period`, `section`, `char_start`, `char_end`.

Those character offsets are load-bearing. They're what makes citations verifiable later, and they're painful to retrofit.

**Stop condition:** chunk table built in Postgres, and a function `resolve(chunk_id) -> exact source text` that round-trips correctly on a random sample of 20.

#### Step 3 — outcome (met 2026-10-03)

Built in three sub-steps (plan and decisions: `.claude/plans/step-3.md`):
- **3a, table spans.** `ingest/tables.py` finds every table by its position in the parsed text and reconciles exactly with edgartools' table list. No table crosses a section.
- **3b, chunker.** `ingest/chunker.py` produces chunks of up to 800 cl100k tokens with up to 100 tokens of text overlap. No chunk crosses a section span, and tables are never split; only table chunks exceed 800 tokens.
- **3c, store.** `db/schema.sql`, `ingest/store.py` and `ingest/load.py`, with `resolve(chunk_id)` as SQL `substr` on the stored filing text.

The stop condition is met on the 24 manifest filings. `spikes/load_report.txt` (commit a5b0804) shows 2,144 chunks, a seeded random sample of 20 resolved exactly, and all 2,144 chunks resolved exactly through `resolve()`. CI runs the same checks on the six fixture filings against its own Postgres.

---

## Phase B — the measurement layer

### Step 4. Write the eval set by hand

This is the highest-value day of work in the whole project. It is written by Rohan, not by a coding agent. Do not generate these with an LLM.

Target ~120 questions, labelled by class:

| Class | Count | Character |
|---|---|---|
| `lookup` | ~40 | Single fact, one chunk. "What was AMD's Q2 gross margin?" |
| `local` | ~35 | One entity, needs surrounding context. "What does INTC say about foundry capacity?" |
| `multi_hop` | ~30 | Requires joining facts that never co-occur. "Which risk factors are new this year and which segments do they touch?" |
| `global` | ~15 | Requires reasoning over the corpus. "What supply-chain themes recur across all three?" |

Each entry: `question`, `class`, `gold_answer`, `gold_chunk_ids` (the passages a correct answer must rest on), and `notes`.

Write the multi-hop and global ones by reading the filings and noticing genuinely hard questions. That's slow. It's also where the project's credibility comes from — an eval set that only contains easy questions proves nothing.

A coding agent may build tooling that helps (a script that validates the JSONL schema and checks every `gold_chunk_id` resolves), but never the questions or answers themselves.

**Stop condition:** `eval/eval_set.jsonl` with 120 labelled entries, committed. Have a friend try to answer five of the multi-hop ones with ctrl-F; if they can, those questions are too easy.

#### Step 4 — amendment (2026-10-04)

At the user's request, subagents drafted a candidate set, `eval/agent_drafted_set.jsonl`.
- Every record is labelled `agent_drafted` and is never counted as human.
- The user reviews drafts with `python -m eval.review`. Accepted ones are copied into `eval/eval_set.jsonl` as `human_verified`, with the user as author.
- Results on agent-drafted records are reported separately from the human set. CLAUDE.md invariant 1 was reworded to match.
- The stop condition above is unchanged: it needs 120 human-written or human-verified entries.
- **Superseded by the user's decision (2026-10-04, later the same day).** A first review accepted all 140 drafts in under two minutes. That's too fast to have checked them, so the user deleted the human set and the review log. The user then chose to run the benchmark on the 140 agent-drafted questions as they are, labelled and reported as agent-drafted. Step 4 is closed on that basis: the human-set stop condition is replaced, not met. The friend Ctrl-F test on five dev multi-hop drafts was skipped on 2026-10-04 by the user's choice. It stays optional before the final benchmark and is listed in the research plan as a threat to validity.

Tooling (`eval/`):
- `validate`: schema, gold chunks resolve, and gold text unchanged; `--db` checks the same through `resolve()` in Postgres;
- `coverage`;
- `search` (with `--show`);
- `review`, which only the user runs, interactively;
- `merge_drafts`.

The plan, and how FinRank was considered, are in `.claude/plans/step-4.md`.

### Step 5. Vector baseline + eval harness

Now build the boring RAG. Embed chunks, store in pgvector, retrieve top-k with metadata filtering by company and period, generate an answer with enforced citations.

Then the harness: a runner that takes an arm name and the eval set, executes every question, and emits scores.

Metrics via Ragas and DeepEval: context precision, context recall, faithfulness, answer relevancy. Plus your own: citation validity (do the cited chunk IDs exist and contain support), p50/p95 latency, cost per query.

Output a JSON results file keyed by `(arm, class)`.

*Amendment, 2026-10-05:* the user dropped DeepEval after the ticket 01 research (docs/research/ragas-deepeval-claude-judge.md). Ragas alone gives the judged metrics, and judge calibration against hand scores is the second opinion. Context precision and recall come from chunk IDs, not an LLM.

**Stop condition:** `python -m eval.run --arm vector` produces a scored results file. You now know how bad plain RAG is on multi-hop, with a number. Write that number down — it's your before.

### Step 6. CI quality gate

Add a workflow that runs the eval on a subset (~40 questions, for speed) on every PR and fails the build if any metric drops more than a threshold below the committed baseline.

Wire Phoenix for tracing. Every retrieval and generation call becomes a span.

**Stop condition:** open a PR that deliberately breaks retrieval (drop k to 1). CI must fail. Revert. This is the moment v1 is real.

#### Step 6 — outcome (met 2026-10-06, step closed 2026-10-07)

Built as eight tickets, each through its own PR (spec: `.scratch/step-6/spec.md`; decisions: ADR-0003 and CLAUDE.md Decisions, Step 6 entries):
- **Gate.** `python -m eval.gate` runs the vector arm's real search offline over a committed snapshot of the corpus database (filings, chunks, embeddings and the gated query vectors). It scores recall@5, recall@10 and the wrong-evidence rate per class and pooled on all 82 answerable `dev` questions, not ~40 (ADR-0003). Any drop against `benchmarks/gate_baseline.json` fails. The CI job `quality-gate` runs it on every PR and every push to `main`, with no API calls.
- **Protection.** Ruleset 24576386 on `main` requires `lint-and-test` and `quality-gate`, with no bypass.
- **Tracing.** Phoenix runs in Docker Compose. With `PHOENIX_COLLECTOR_ENDPOINT` set, each eval question is one trace: filter, query embedding, vector search, answer generation, and an EVALUATOR span per judged metric with an LLM span per judge call. Unset, nothing is traced.

The stop condition is met. PR #4 made the search return 1 chunk. `quality-gate` failed with exit 4 and named all 10 recall numbers that dropped; pooled recall@10 fell from 0.890 to 0.418 (agent-drafted questions). GitHub blocked the merge, and the PR was closed unmerged (CI run 37456438739).

---

## Phase C — the graph

Design detail for steps 7–13 is in `docs/GRAPH-LAYER.md`. Where the two differ, the stop conditions in this file govern.

### Step 7. Ontology as code

Define node labels, relationship types, and required properties in one module. Generate the schema string for prompts from that module — never hand-maintain a copy.

Write the constraints: uniqueness on `Company.cik`, `Filing.accession_no`; and the provenance constraint, which you'll enforce in the write path since most graph DBs can't express "every edge must have an evidence pointer" declaratively.

**Stop condition:** schema module exists, database constraints applied, a `validate_graph()` function that fails loudly on any unevidenced node or edge.

#### Step 7 — outcome (met 2026-10-07)

Built as five tickets, each through its own PR; the CI runs named are the push-to-`main` runs after each merge (spec: `.scratch/step-7/spec.md`; decisions: ADR-0004 and CLAUDE.md Decisions, Step 7 entries):
- **Ontology.** `graph/ontology.py` defines 14 labels (4 structural, 10 extracted) and 20 edge types with their endpoints and typed properties. The schema text for Step 8's prompt and its SHA-256 are generated from it (`ONTOLOGY_VERSION = 1`, hash `9f2687c2…57fb37`). PR #12, CI run 37627699575.
- **Constraints.** `apply_constraints()` derives 14 uniqueness constraints from the ontology, reads `SHOW CONSTRAINTS` back, fails on a missing or extra one, and records the version and hash on one `:GraphMeta` node. A `neo4j-test` service runs locally and in CI. PR #13, CI run 37636783415.
- **Write path.** `write_batch()` checks a whole batch against the ontology and evidence rules before any database call and writes nothing if anything fails, listing every violation. PR #14, CI run 37642667516.
- **Validation.** `validate_graph()` and `python -m graph.validate` read the whole graph back and run ten checks, including that every extracted node and edge cites an existing chunk and that each Chunk node is in Postgres under the same filing. Exit 4 on any violation. PR #15, CI run 37649669109.

The stop condition is met. On the local Neo4j, `apply_constraints()` created the 14 constraints and `:GraphMeta`, and `python -m graph.validate` exited 0 on the empty graph, with the Postgres check run. On `neo4j-test`, the valid test graph passed; deleting TSMC's `EVIDENCED_BY` edge exited 4 under `node_evidence`, and removing the `SUPPLIES` edge's `chunk_ids` exited 4 under `edge_properties`, each naming the item (BUILD-LOG, 2026-10-07, ticket 05). Every other kind of broken graph is one of the 17 broken-case tests in `tests/test_graph_validate.py`, each failing exactly its own check (PR #15). Ticket 05 also stopped the validator flooding stderr with Neo4j's "does not exist" notifications.

### Step 8. Extraction

Prompt 1 in `prompts/extract.v1.md`. Feed it one chunk at a time along with the allowed labels and relation types. It returns candidate triples with `evidence_span` and `confidence`.

Two rules that determine whether this works:
- Validate every returned label and relation type against the ontology before doing anything else. Discard, log, and count violations. Report the violation rate.
- Run on one company first. Read 30 extracted triples yourself, line by line, against the source text. You will find the prompt is wrong in a way you didn't anticipate. Fix it, re-run, read 30 more.

**Stop condition:** extraction over one 10-K, manual accuracy check on 30 triples at or above ~85%, zero ontology violations reaching the write path.

_Note (2026-10-09): the prompt lives at `prompts/extract/v1.md`, following the `prompts/answer/v1.md` convention. "At or above ~85%" applies to the last review round; every round is reported._

### Step 9. Entity resolution

Order matters, and the LLM comes last:

1. **Authoritative keys.** CIK for companies, XBRL concept tags for metrics. These are ground truth; never second-guess them.
2. **Normalization.** Case, whitespace, legal suffixes (`Inc.`, `Corp`, `Ltd`, `plc`).
3. **Blocking.** Generate candidate pairs only within the same label and above an embedding similarity threshold. Never compare all pairs.
4. **LLM adjudication** on what survives, using Prompt 2. Must return evidence.
5. **Merge log.** Append-only, with the deciding evidence. Every merge reversible.

Hand-label 100 candidate pairs as same/different. Score your resolver against them.

**Stop condition:** ER precision and recall measured and written into the README. This number is your credibility.

### Step 10. Load the graph

Write nodes and edges through a single guarded write path that rejects anything unevidenced. Run `validate_graph()` after load.

Build the `PERSISTS_AS` linkage between periods — matching a risk factor in FY2025 to its FY2024 counterpart. This is itself an entity-resolution problem and it's what makes the multi-hop questions answerable.

**Stop condition:** full graph loaded across all three companies. Zero constraint violations. Run three multi-hop questions as hand-written Cypher and confirm the graph can actually answer them. If it can't, your ontology is wrong — go back to step 7. Better to find this now.

### Step 11. Graph retrieval arms

**Local search:** link the question to anchor entities, expand 1–2 hops, collect the `EVIDENCED_BY` chunks, generate from those.

**Traversal:** text-to-Cypher with the guards — live schema injected, generated query validated against the ontology before execution, read-only credentials, statement timeout, `LIMIT` enforced by the executor. On failure: one repair attempt with the error message, then fall back to local search and log the fallback.

Track Cypher validity rate from day one.

**Stop condition:** `--arm graph_local` and `--arm graph_traversal` both run the full eval set end to end. Cypher validity rate reported.

### Step 12. Communities and global search

Leiden clustering at two resolution levels over the entity graph, run in Python (`leidenalg` or `graspologic`). Summarize each community with Prompt 3. Embed the summaries.

Global search: score community reports for query relevance, generate partial answers from the top ones, reduce to a final answer. Cap the fan-out and log tokens per query — this arm is where cost runs away.

**Stop condition:** `--arm graph_global` scores meaningfully above zero on the 15 global questions, with traceable provenance from claim back to chunk.

### Step 13. Router and the benchmark

The router: classify each query into one of the four classes and dispatch. Start with a cheap LLM classifier and few-shot examples. Log every routing decision as a span.

Measure router accuracy against your labelled eval set — you already have the gold classes, so this is free.

Then run the full matrix: four arms (`vector`, `graph_local`, `graph_traversal`, `graph_global`) plus the router, × four classes × all metrics. Generate `benchmarks/results.md` from the harness, and have CI regenerate it.

**Stop condition:** the results table is committed and you can explain every cell in it, including the ones where graph loses.

---

## Phase D — making it legible

### Step 14. Interface, then write-up

Streamlit UI, following `docs/design/filingintel-demo.html`: question box, answer with inline citations that expand to the source passage, the reasoning path when a graph arm was used, and the live eval metrics for that query.

Then the README, which is what most people will actually read:

1. What it does, in three sentences, with a screenshot.
2. The benchmark table, up top — not buried at the bottom.
3. The honest interpretation, including where graph lost and what it cost.
4. Architecture, briefly. Link the diagrams.
5. Numbers most projects don't publish: ER precision/recall, Cypher validity rate, router accuracy, extraction violation rate.
6. What you'd do differently.

Point 6 matters more than it looks. It's the difference between a portfolio project and a tutorial follow-along.

---

## Timing

| Phase | Steps | Time |
|---|---|---|
| A — foundation | 1–3 | ~4 days |
| B — measurement | 4–6 | ~5 days (step 4 is a full day alone) |
| C — graph | 7–13 | ~2 weeks |
| D — legible | 14 | ~3 days |

Call it five weeks at a real pace alongside a job. Steps 9 and 11 will take longer than you expect — entity resolution and text-to-Cypher are where the actual engineering lives.

---

## Where it goes wrong

- **Skipping step 4** and writing the eval set after the graph. You'll unconsciously write questions your system already answers.
- **Expanding the corpus** when results are mediocre. More filings won't fix a bad ontology; it'll just make the failure slower to diagnose.
- **Trusting extraction output** without reading it. Read thirty triples by hand. Then read thirty more.
- **Letting the LLM invent the schema.** A graph with 200 ad-hoc relation types is unqueryable and there's no recovering from it without a rebuild.
- **Reporting only wins.** A benchmark table where the new thing beats the old thing on every row reads as fabricated, because it usually is.

---

## OSS that falls out, in the order you'll hit it

- Step 8 or 11: rough edges in LangChain's graph modules — schema serialization, Cypher validation. Small, real PRs.
- Step 13: Ragas has no graph-retrieval metric. Your path-precision implementation is a substantive contribution, and by then you'll have a benchmark demonstrating it works.
- Anywhere: `edgartools` / `sec-parser` parsing failures on real filings. Bug reports with reproductions are welcome contributions and cost you nothing extra, since you had to fix them anyway.