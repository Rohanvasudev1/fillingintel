# FilingIntel

Citation-grounded question answering over SEC filings. Ask what NVDA, AMD or INTC disclose in their 10-K and 10-Q filings, and every claim in the answer links to the exact passage it came from.

The project compares four retrieval strategies (vector search, graph neighbourhood search, Cypher traversal and community summaries) on one hand-written set of ~120 questions, and publishes where each one wins and loses.

**Status:** in progress. Ingest, chunking and `resolve(chunk_id)` are built and verified on 24 filings (RUNBOOK Steps 2–3). Step 4 is closed with 140 agent-drafted eval questions, used without human review and labelled as such wherever results appear. The first measured result is entity resolution (Step 9, below). Retrieval benchmark results will appear here once they come from real runs.

## Docs

- [Architecture](docs/ARCHITECTURE.md): system design
- [Runbook](docs/RUNBOOK.md): build order and milestones
- [Graph layer](docs/GRAPH-LAYER.md): ontology, entity resolution and graph retrieval design

## Entity resolution (Step 9)

The resolver decides which extracted nodes name the same real-world entity across the 24 filings (6,765 nodes). Cheap rules flag candidate pairs within one label: equal names after normalization, a RapidFuzz `token_set_ratio` of 85 or more, an acronym match, or a top-3 voyage-4-large neighbour. claude-haiku-5-5 (effort `high`, prompt `resolve/v2`) then judges each candidate. A merge needs a `same` answer, quotes from both nodes found in their own chunks, and a clean group: a group holding a `related` or `distinct` pair, or more than 5 names, is held back. Run 2 adjudicated 3,821 pairs and made 578 merges.

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

The resolver never merged wrongly in the sample, but it misses most true matches. Of the nine candidate-stratum misses, five were pairs Haiku called `same` whose group the safety rule held back (large clusters such as AMD Versal, AMD's MI308 products and the US export-licensing rules), and four got `unsure`. The three near-stratum matches were never sent to the adjudicator. The rest stratum's 15 pairs carry most of the weight and held no match, so recall there is not measured well.

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