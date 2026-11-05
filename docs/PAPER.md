# Graph retrieval against vector search on SEC filings: a pre-registered benchmark

Rohan Vasudev, November 2026. Draft for review.

## Abstract

FilingIntel answers questions about the 10-K and 10-Q filings of NVIDIA, AMD and Intel, and every sentence it shows cites the exact filing passage it came from. I built it to measure when graph-based retrieval beats plain vector search, and what the graph costs. Four retrieval arms and a router were compared on 140 agent-drafted questions, with hypotheses, thresholds and a class-to-arm map fixed before any run, and a held-out `test` split of 47 questions scored once. Vector search won. On the 38 answerable `test` questions it found 0.923 of the gold passages in its top 10. The best graph arm found 0.697, and the best router 0.711, and every pooled difference against vector has a 95% interval that excludes 0. The graph itself was built cleanly: no ontology violation reached the write path, and every citation from the community-summary arm traced back through the graph to a filing chunk. The losses come from which passages the graph arms retrieve.

## 1. Question

On SEC filings for three semiconductor companies, when does graph-based retrieval beat vector retrieval, and at what cost? The research plan (`docs/RESEARCH-PLAN.md`, approved 2026-10-04) set out to map wins and losses. It stated six hypotheses in advance, at least one predicting a graph loss.

## 2. System

- **Corpus.** 24 filings from EDGAR: for each company, the two most recent complete fiscal years, each a 10-K and three 10-Qs. edgartools parses each filing into sections whose character offsets are recorded as the text is built, so `resolve(chunk_id)` returns the exact cited text. The chunker splits sections into 2,144 chunks, stored in Postgres.
- **Answers.** claude-sonnet-5-5 writes every answer from at most 10 chunks. Each sentence must cite a retrieved chunk, and code drops any sentence that does not before the reader sees it.
- **Vector arm (the control).** voyage-4-large embeddings in pgvector, exact search, and a company and period filter parsed from the question text.
- **Knowledge graph.** claude-haiku-5-5 extracted entities and edges from every chunk into a fixed ontology in Neo4j. Every node and edge stores the chunk and quoted span that evidence it, and the write path rejects anything without one. Entity resolution merges names across filings through an append-only merge log. A persistence linker connects each risk factor to its earlier version. Leiden finds two levels of communities, and each community of three or more members gets a report that cites chunks.
- **Graph arms.** `graph_local` links the question to entities and ranks the chunks their edges cite. `graph_traversal` has the LLM write Cypher, which runs only after five guards in code, with one repair and a fallback. `graph_global` answers by map-reduce over community reports and their evidence chunks.
- **Router.** A claude-haiku-5-5 classifier assigns each question a class, declines investment advice itself, and sends the rest to an arm by a routing map.
- **Interface.** A local Streamlit app shows the answer, each cited passage highlighted in its filing, and a trace of how the arm found it.

## 3. Method

- **Questions.** 140 agent-drafted questions with gold chunks, in six classes: lookup, local, multi_hop, global, decline and unanswerable. No person wrote or reviewed them; every result carries that label. About 30% were held out as `test` by a hash of the question text.
- **Tuning.** All tuning (prompts, k, hop count, N communities, the router) used `dev`. Arms, prompts and routing maps were then frozen in a committed record, and `eval.run --final` refuses to score `test` without a match.
- **Metrics.** Recall and precision from chunk IDs with no LLM involved, a wrong-evidence rate against labelled hard negatives, faithfulness, relevancy and citation support from a gpt-6-luna judge through Ragas, and cost and latency per question.
- **Statistics.** Fixed-seed bootstrap 95% intervals on every cell, and paired differences against vector on the same questions. A difference counts only when its interval excludes 0. A class with fewer than 10 `test` questions is reported as directional.
- **Reproducibility.** Every API response is cached, so reruns replay answers exactly. Each run file records its commit, models, efforts, prompt hashes and config. `benchmarks/results.md` and the README tables are generated from committed run files, and CI fails if they drift.

## 4. Results on `test`

Pooled recall@10 over 38 answerable questions, with the paired difference against vector:

| row | pooled recall@10 [95%] | vs vector [95%] |
| --- | --- | --- |
| vector | 0.923 [0.864, 0.974] | - |
| router (dev-best map) | 0.711 [0.579, 0.833] | -0.213 [-0.336, -0.101] |
| graph_traversal | 0.697 [0.561, 0.820] | -0.226 [-0.351, -0.110] |
| router (pre-registered map) | 0.654 [0.522, 0.776] | -0.270 [-0.399, -0.149] |
| graph_local | 0.614 [0.469, 0.754] | -0.309 [-0.439, -0.184] |
| graph_global | 0.439 [0.298, 0.583] | -0.485 [-0.623, -0.349] |

By class:

- **Lookup (13 questions).** Every arm except `graph_global` found all or nearly all gold passages. Single facts do not need a graph.
- **Local (13).** `graph_local` fell from 0.909 on `dev` to 0.500 on `test`, with every setting frozen. Both routers send local questions to it and inherit the loss.
- **Multi-hop (7, directional).** `graph_traversal`, built for these questions, trailed vector 0.524 to 0.798.
- **Global (5, directional).** `graph_global` found 0.333 against vector's 0.800. On the 14 questions it answered in full it cost $0.076 a question against vector's $0.031.
- **Decline and unanswerable (9).** Every row declined the advice questions and said the filings lack the answer to the unanswerable ones (judged, directional).

## 5. Hypotheses

| hypothesis | verdict on `test` |
| --- | --- |
| H1, vector holds on lookups (recall@5 within 0.05) | supported |
| H2, traversal beats vector on multi-hop by 0.10 | rule not met (directional): -0.274 [-0.571, +0.012] |
| H3, period confusion is the main wrong-evidence failure | not supported: holds for vector, `graph_local` and `graph_traversal`, not for `graph_global` (1 of 3) |
| H4, community summaries win global relevancy by 0.10 | rule not met (directional): +0.004 [-0.087, +0.096] |
| H5, every graph arm costs at least 2x vector's p95 latency and more per question | not supported: `graph_local` was faster and cheaper |
| H6, the router is at least 80% accurate on `dev` and declines every advice question | supported: 76 of 88, and 6 of 6 declined |

## 6. Build quality

These figures describe the pipeline rather than the answers. The `test` routing figure is the classifier's accuracy against the agent-drafted class labels.

- Extraction: 26,915 candidate triples over 24 filings, 0 ontology violations reaching the write path, 327 rejected before it (1.2%). Two review rounds of 30 sampled edges each, judged by me: 30 of 30 correct in both.
- Entity resolution: precision 1.000 and recall 0.783 [0.600, 0.947] on 100 pairs labelled by gpt-6-luna.
- Risk-factor persistence links: precision 0.958, recall 0.428, on 100 pairs labelled by gpt-6-luna.
- Text-to-Cypher validity on `dev`: 88 of 93 first pass, 93 of 93 after one repair.
- Router accuracy on `test`: 38 of 42, 0.905 [0.779, 0.962].
- `graph_global` provenance: 0 broken citation paths in 163 (`dev`) and 82 (`test`).
- Judge corruption test: of planted errors (a changed number, a swapped fiscal period, a wrong citation, an added sentence), citation support caught every one, and faithfulness caught 68 of the 72 it can see.
- Engineering: 2,966 passing tests, an offline retrieval quality gate as a required check on `main`, and Phoenix traces for every stage.

API spend recorded in the build log was about $37 in all: the vector baseline and its repeat $6.80, extraction $6.27, entity resolution $3.21, persistence links $0.93, the first two graph arms $8.47, communities and `graph_global` $2.44, the router, the judge tests and the `test` runs $8.44, and the interface $0. Small items outside those totals, such as node embeddings, add a few cents.

## 7. Limitations

- **Agent-drafted questions.** Agents in the same project drafted the questions and their gold passages, and no person reviewed them. This is the largest caveat on every number.
- **Small classes.** With 7 multi-hop and 5 global `test` questions, the classes the graph was meant to win can only be directional.
- **Uncalibrated judge.** The calibration tools and draw are committed but I did not score the draw, so faithfulness, relevancy and citation support are the judge's numbers, unchecked against a person.
- **Graph context not passed on.** `graph_local` and `graph_traversal` hand the answer model chunk text only, not the paths that selected them. For those two arms the comparison is between retrievers.
- **Narrow scope.** Three companies in one sector, two fiscal years. The findings may not transfer.

## 8. Conclusion

On this corpus and question set, a well-built vector retriever with a parsed company and period filter beat every graph method tried, including the arms designed for multi-hop and global questions. The graph was accurate and auditable, but accuracy in the graph did not become better retrieval. The next experiments, in order: test the graph's ceiling with hand-written Cypher on the multi-hop and global gold passages, pass graph paths to the answer model, write or verify 30 questions per class by hand, and calibrate the judge.

Full tables and sources: `benchmarks/results.md`. Build history: `docs/BUILD-LOG.md`.
