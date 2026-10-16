# FilingIntel

Citation-grounded question answering over SEC filings. Ask what NVDA, AMD or INTC disclose in their 10-K and 10-Q filings, and every claim in the answer links to the exact passage it came from.

The project compares four retrieval strategies (vector search, graph neighbourhood search, Cypher traversal and community summaries) on one set of agent-drafted questions (140, used without human review), and publishes where each one wins and loses.

**Status:** in progress. Ingest, chunking and `resolve(chunk_id)` are built and verified on 24 filings (RUNBOOK Steps 2–3). Step 4 is closed with 140 agent-drafted eval questions, used without human review and labelled as such wherever results appear. Measured so far: entity resolution (Steps 9 and 9b), the two graph retrieval arms against vector search on the `dev` split (Step 11) and community summaries with global search (Step 12), below. The full benchmark on the held-out `test` split comes in Step 13.

## Docs

- [Architecture](docs/ARCHITECTURE.md): system design
- [Runbook](docs/RUNBOOK.md): build order and milestones
- [Graph layer](docs/GRAPH-LAYER.md): ontology, entity resolution and graph retrieval design

## Entity resolution (Steps 9 and 9b)

The resolver decides which extracted nodes name the same real-world entity across the 24 filings (6,765 nodes). Cheap rules flag candidate pairs within one label: equal names after normalization, a RapidFuzz `token_set_ratio` of 85 or more, an acronym match, or a top-3 voyage-4-large neighbour. claude-haiku-5-5 (effort `high`, prompt `resolve/v2`) then judges each candidate. A merge needs a `same` answer and quotes from both nodes found in their own chunks. Step 9 also needed a clean group: a group holding a `related` or `distinct` pair, or more than 5 names, was held back whole. Step 9b cuts such a group instead, removing the fewest `same` answers so that no `related` or `distinct` pair shares a piece, and merges each piece of up to 12 names. Run 3 adjudicated 3,821 pairs (all answers replayed from run 2's cache) and made 602 merges.

### Current figures: scoring round 2 (Step 9b)

All figures here are **agent-labelled (gpt-6-luna)**, as in round 1 below. The two rounds are scored on different 100-pair draws, so the gap between them is not an exact effect size.

| Round | Rule | Commit | Config | Precision | Recall | Adjudicator accuracy |
|---|---|---|---|---|---|---|
| 1 (Step 9) | hold back groups with a contradiction or over 5 names | `68b35fd` | `7a5aa8de76e5` | 1.000 [1.000, 1.000] | 0.387 [0.199, 0.611] | 0.917 [0.850, 0.983] |
| **2 (Step 9b, current)** | **group cut, 12-name limit** | `99882ce` | `721ca77c90bc` | **1.000 [1.000, 1.000]** | **0.783 [0.600, 0.947]** | 0.900 [0.817, 0.967] |

Intervals are 95% bootstrap intervals, from 10,000 resamples within strata. A bootstrap interval cannot move off 1.000 when no sampled merge is wrong; the Wilson 95% interval on the merges sampled is [0.72, 1.00] for round 1 (10 of 10) and [0.82, 1.00] for round 2 (18 of 18). In round 2 the resolver merged 18 of the 23 candidate-stratum pairs the labeller called the same, and none it called different. All five misses are the adjudicator's own answers (one `distinct`, two `related`, two `unsure`), not the cut. In two of them the labeller called the $5.0 billion and $8.0 billion 364-day credit facilities the same agreement, which looks like a label error; the labels are left as written. Round 2's near stratum held no true match (round 1's held three that no rule found), so the share of matches the rules found is 1.000 in round 2 against 0.735 in round 1, and part of the recall gain comes from the draw.

Before the freeze, the tuning check compared run 2 and run 3 on the 100 tuning pairs. Two more pairs merge, and agreement with the labels stays at 94 of 100. One of the two is labelled not same: Intel's "CHIPS Act" (its glossary spells it out without "and Science") merged with the CHIPS and Science Act. It merged because the 12-name limit admits that six-name group, and the user accepted it before the freeze (`frozen-2.json`).

Provenance, round 2: run `run3.json`; `scoring2-score1.json`; `frozen-2.json`; `tuning-check.json`; `resolve/v2@5b5e4a7d`; `label/v2@a7f163e9`; ontology version 3.

### Round 1 (Step 9)

All figures below are **agent-labelled (gpt-6-luna)**. The RUNBOOK asked for 100 hand-labelled pairs; the user chose an agent labeller instead (gpt-6-luna at effort `high`, prompt `label/v2`), and no human checked its labels. The rules and prompts were tuned on a separate 100-pair tuning set, frozen, and then scored once on 100 fresh pairs.

| Metric (scoring set, 100 pairs) | Value | 95% interval |
|---|---|---|
| Pairwise precision | 1.000 | [1.000, 1.000] bootstrap; Wilson on 10 of 10 merges sampled: [0.72, 1.00] |
| Pairwise recall | 0.387 | [0.199, 0.611] |
| Share of true matches the candidate rules found | 0.735 | [0.529, 1.000] |
| Adjudicator accuracy on the candidate pairs it judged | 0.917 (55 of 60) | [0.850, 0.983] |

Precision and recall are weighted by stratum (population over pairs labelled) with a 10,000-resample bootstrap within strata. Raw counts:

| Stratum | Population | Labelled | Same (labeller) | Merged | Correct merges |
|---|---|---|---|---|---|
| candidate (some rule fired) | 3,829 | 60 | 19 | 10 | 10 |
| near (just below a rule's cutoff) | 3,648 | 25 | 3 | 0 | 0 |
| rest | 144,581 | 15 | 0 | 0 | 0 |

The resolver never merged wrongly in the sample, but it misses most true matches. Of the nine candidate-stratum misses, five were pairs Haiku called `same` whose group the safety rule held back (large clusters such as AMD Versal, AMD's MI308 products and the US export-licensing rules), and four got `unsure`. The three near-stratum matches were never sent to the adjudicator. The rest stratum's 15 pairs carry most of the weight and held no match, so recall there is not measured well. Step 9b cuts held-back groups instead of dropping them; round 2 above is its score on a fresh draw.

Provenance: scored at commit `68b35fd`; config hash `7a5aa8de76e5` (fuzzy cutoff 85, top_k 3, rare-word rule off, "label: name" embedded); `resolve/v2@5b5e4a7d`; `label/v2@a7f163e9`; ontology version 3. Files: `benchmarks/resolution/scoring-score1.json`, `run2.json`, `tuning-report.json`, `frozen.json`.

## Risk-factor persistence (Step 10)

Each risk factor in a 10-K is linked to the same risk in the company's previous 10-K, and each risk factor in a 10-Q to the same risk in the most recent 10-K before it (12 filing pairs, 767 later risk factors). Identical normalized titles link with no LLM call (319 links). Every other later factor's top 3 voyage-4-large neighbours go to claude-haiku-5-5 (effort `high`, prompt `persist/v1`), and a link needs a `same` answer with quotes from both factors found in their own chunks. A later factor keeps at most one predecessor: its exact-title match, else the highest-ranked `same`. Run 1 made 621 links (302 adjudicated).

All figures here are **agent-labelled (gpt-6-luna)**: gpt-6-luna at effort `high` (prompt `persist_label/v1`) labelled 100 pairs, and no human checked its labels. The settings were frozen before the draw. The pairs were drawn 60 / 25 / 15 from the candidate stratum (exact-title or adjudicated pairs, 1,663), the near stratum (neighbours ranked 4 to 6, 1,344) and the rest (64,018), and the links were scored once. The labeller never saw the linker's output.

| Links | Commit | Config | Precision | Recall | Adjudicator accuracy |
|---|---|---|---|---|---|
| run 1 | `26f1299` | `36673beebe68` | **0.958 [0.864, 1.000]** | **0.428 [0.298, 0.572]** | 0.727 [0.591, 0.864] |

Intervals are 95% bootstrap intervals, from 10,000 resamples within strata. The Wilson 95% interval on the sampled links is [0.80, 0.99] (23 of 24 right). The wrong link is an exact-title match on a generic heading, Intel's "Catastrophic events", which the labeller would not call the same risk from the heading alone.

Recall is low, and it is reported as measured, not tuned. The labeller called 44 candidate-stratum pairs the same risk, and 23 of them are links. Of the 21 misses, 9 are `same` answers dropped by the one-predecessor rule, because the later factor was already linked to another earlier factor. The labeller often calls one later factor the same risk as several earlier ones, where the filer merged or split factors. The other 12 are `distinct` or `unsure` answers. The near stratum held 5 matches the adjudicator never saw. The share of true matches in the candidate stratum is 0.819 [0.706, 0.954].

Provenance: `benchmarks/persistence/` holds `run1.json`, `links.jsonl`, `frozen.json`, `scoring-draw.json`, `scoring-labels.jsonl`, `scoring-label-run1.json` (labelling cost $0.05) and `scoring-score1.json`. Prompts `persist/v1@5eaa7a2c` and `persist_label/v1@9a57ed5c`; ontology version 3.

## Knowledge graph (Step 10)

`python -m graph.load --rebuild` rebuilds the whole Neo4j graph in one transaction from Postgres, the extract files, the alias map and the persistence links, then validates it. Load run 3 (commit `355b332`, ontology version 3) passed `validate_graph()` with zero violations, the Postgres chunk check included.

| Nodes | Count | | Edges | Count |
|---|---|---|---|---|
| Chunk | 2,144 | | EVIDENCED_BY | 10,736 |
| MetricValue | 3,779 | | MEASURES | 3,779 |
| RiskFactor | 1,405 | | REPORTS | 3,777 |
| Product | 278 | | PART_OF | 2,144 |
| Organization | 147 | | DISCLOSES | 1,405 |
| Agreement | 120 | | PERSISTS_AS | 621 |
| Regulation | 98 | | OFFERS | 317 |
| LegalProceeding | 47 | | AFFECTS | 235 |
| Person | 27 | | PARTY_TO | 209 |
| Filing | 24 | | SUBJECT_TO | 182 |
| Period | 24 | | INVOLVED_IN | 134 |
| Facility | 23 | | CONCERNS | 51 |
| MarketPlatform | 19 | | COMPETES_WITH | 47 |
| Segment | 17 | | HOLDS_ROLE_AT | 34 |
| Company | 3 | | OWNS | 33 |
| | | | SUPPLIES | 27 |
| | | | FILED, COVERS_PERIOD | 24 each |
| | | | CUSTOMER_OF | 20 |
| | | | HAS_MARKET_PLATFORM | 19 |
| | | | HAS_SEGMENT | 16 |

PERSISTS_AS holds 319 exact-title links and 302 adjudicated ones (`persist/v1@5eaa7a2c`).

| Left out or flagged | Count | Detail |
|---|---|---|
| Conflicts | 1 | Intel's stake in Altera: 0.49 kept (FY2025 10-K) over 1.0 (Q2 FY2025 10-Q) |
| Skipped metric values | 2 | Intel figures whose MEASURES edge extraction dropped |
| Dropped edges | 6 | 5 `CONCERNS` edges whose merged end is a filer's Company, which the ontology does not allow; 1 `REPORTS` edge of a skipped metric value |
| Chunk gap | 1 | `0000050863-24-000076:0017`, which extraction failed on |

Provenance: `benchmarks/graph/load-run3.json` holds the SHA-256 of the alias map, the link file and every extract file.

## Graph retrieval arms (Step 11)

Two graph arms hand at most 10 ranked chunks to the same answer prompt as `vector`, so they differ only in which chunks they pick (ADR-0007). `graph_local` links the question to up to 20 anchor nodes, by name match or embedding similarity, and ranks their evidence chunks by edge support (1 hop). `graph_traversal` has claude-sonnet-5-5 write one Cypher query from the generated schema. The query passes a guard in code before it runs read-only, gets one repair on failure, and falls back to `graph_local` after a second failure.

**Agent-drafted questions, `dev` split, graph arms tuned on `dev`** (hop count and one prompt revision). Context recall@10 per class (gold chunks in the top 10 / gold chunks). Graph runs at commit `375efac` on load run 4 (ontology 4); `vector` is the Step 5 baseline at `96e8c69`. All three use answer prompt `answer/v1`.

| Arm | lookup (27) | local (22) | multi_hop (23) | global (10) | pooled (82) |
|---|---|---|---|---|---|
| `vector` | **1.000** | **0.977** | **0.717** | **0.800** | **0.890** |
| `graph_local` | 0.963 | 0.909 | 0.627 | 0.425 | 0.789 |
| `graph_traversal` | 0.963 | 0.818 | 0.703 | 0.367 | 0.778 |

Paired against `vector` (fixed-seed bootstrap, 95% intervals), pooled recall@10 is −0.102 [−0.177, −0.030] for `graph_local` and −0.112 [−0.195, −0.032] for `graph_traversal`. Graph loses here, and no class difference in a graph arm's favour has an interval that excludes 0. `graph_traversal` on multi-hop questions, the case it was built for, is level with `vector` (−0.014 [−0.217, 0.181]), not ahead. Global questions are the largest loss for both arms; community summaries (Step 12) are meant for them. Judged answer relevancy (uncalibrated, gpt-6-luna) is lower for both graph arms, pooled: −0.025 [−0.041, −0.009] (`graph_local`) and −0.051 [−0.087, −0.021] (`graph_traversal`).

**Cypher validity rate** (`cypher/v1@44e50e3f`, 93 `dev` questions, commit `5a1681d`, `benchmarks/graph/cypher-validity-run1.json`; the full traversal run at `375efac` replayed the same queries with the same counts):

| First-pass valid | Valid after one repair | Non-empty | Fallback to `graph_local` |
|---|---|---|---|
| 0.946 (88 of 93) | 1.000 | 1.000 | 0 |

A query counts as valid when it passes the ontology check and runs without error inside the timeout. A revised prompt, `cypher/v2`, was valid first time on all 93 questions but retrieved worse (pooled recall@10 0.727), so `v1` is used. These are `dev` figures; `test` is scored once, in Step 13.

## Community summaries and global search (Step 12)

Leiden clustering (`graspologic-native` 1.3.1, fixed seed, modularity at resolution 1.0) runs over the extracted entities, with edges weighted by evidence chunks and by chunks two entities share. It gives 35 top-level and 122 lower-level communities (median 13.5 members at the lower level). claude-haiku-5-5 wrote a report for each of the 144 communities with 3 or more members. Every report sentence cites a chunk inside its community, and uncited sentences are dropped (1,477 kept, 57 dropped). The reports cost $0.27.

`graph_global` answers by map-reduce (ADR-0008). It picks the 4 lower communities whose reports sit closest to the question. One map call per community reads the report and 6 evidence chunks and writes scored points citing those chunks. A reduce call turns the points into the answer. Reports are never cited; every claim cites a filing chunk.

**Agent-drafted questions, `dev` split, N tuned on `dev`** (it tied at every value tried). Run at commit `5bb0799` on load run 5 (ontology 5); `vector` is the Step 5 baseline at `96e8c69`. `graph_global` answers with its own reduce prompt, so judged scores compare whole pipelines.

| 10 global questions | `vector` | `graph_global` | Paired difference [95% interval] |
|---|---|---|---|
| Recall@10 | **0.800** | 0.458 [0.300, 0.617] | −0.342 [−0.500, −0.183] |
| Recall@5 | **0.592** | 0.333 | −0.258 [−0.508, −0.025] |
| Answer relevancy (judged, uncalibrated) | **0.781** | 0.694 | −0.088 [−0.160, −0.027] |
| Faithfulness (judged, uncalibrated) | 0.787 | 0.818 | +0.031 [−0.115, 0.170] |
| Citation support (judged, uncalibrated) | **0.974** | 0.931 | −0.043 [−0.085, −0.009] |
| Cost per question | **$0.035** | $0.082 | |

Community search did find relevant evidence: its recall@10 interval excludes 0, and a check traced all 163 kept citations through a selected community to their chunks with no break. It still lost to vector search on the questions it was built for. The research plan predicted a relevancy gain of at least 0.10 (H4); the measured change is a loss. Both arms declined all 6 advice questions and answered "not found" on all 5 unanswerable ones (directional, n under 10). Lookup, local and multi-hop questions, and the `test` split, wait for Step 13.

## Running it

Needs Docker, [uv](https://docs.astral.sh/uv/) and a `.env` made from `.env.example`. `EDGAR_USER_AGENT` must hold a real name and email, as the SEC requires.

```bash
docker compose up -d                                          # Postgres and Neo4j
uv run --env-file .env python -m ingest.corpus                # download, parse, report; writes data/parsed/
uv run --env-file .env python -m ingest.load --verify         # load Postgres, check resolve() on every chunk
uv run --env-file .env pytest                                 # tests (database tests need DATABASE_URL)
```

## Stack

Python, FastAPI, LangGraph, Postgres with pgvector, Neo4j, Ragas, DeepEval, Phoenix, GitHub Actions, Streamlit.

## Scope

Three companies, 10-K and 10-Q filings only. This is a research and engineering project; it does not give investment advice.