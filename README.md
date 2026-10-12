# FilingIntel

Citation-grounded question answering over SEC filings. Ask what NVDA, AMD or INTC disclose in their 10-K and 10-Q filings, and every claim in the answer links to the exact passage it came from.

The project compares four retrieval strategies (vector search, graph neighbourhood search, Cypher traversal and community summaries) on one hand-written set of ~120 questions, and publishes where each one wins and loses.

**Status:** in progress. Ingest, chunking and `resolve(chunk_id)` are built and verified on 24 filings (RUNBOOK Steps 2–3). Step 4 is closed with 140 agent-drafted eval questions, used without human review and labelled as such wherever results appear. The first measured result is entity resolution (Steps 9 and 9b, below). Retrieval benchmark results will appear here once they come from real runs.

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