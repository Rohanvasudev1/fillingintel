# Leiden communities and GraphRAG global search (Step 12)

Research note for Step 12, written 2026-10-15. Step 12 adds Leiden communities at two levels, community summaries embedded with voyage-4-large, and a `graph_global` arm that answers by map-reduce over community reports. This note covers five things: which Leiden library to use, how Microsoft GraphRAG builds communities, reports and global search, what independent evaluations found, how sparse graphs with isolated nodes are handled, and what it all costs.

Sources: PyPI JSON metadata (read 2026-10-15); `microsoft/graphrag` at tag `v3.3.0` (2026-10-08); `graspologic-org/graspologic-native` at `HEAD` (last push 2026-06-14) and its releases; `igraph/python-igraph` at tag `1.0.0`; `networkx/networkx` at tag `networkx-3.6.1`; the leidenalg reference docs and its `CHANGELOG`; the GraphRAG docs at microsoft.github.io/graphrag; Microsoft Research blog posts; arXiv 2404.16130v2, 2502.11371v3, 2506.05690v3 and 2506.06331. Source files were read with `gh api` at the tags named. The project's own numbers come from `uv.lock` and `retrieve/pricing.py`.

Claims marked "(ran)" were checked on 2026-10-15 with `uv run --no-project --python 3.11 --with leidenalg==0.12.0 --with igraph==1.0.0 --with graspologic-native==<v>` on Zachary's karate club graph (34 nodes) plus 5 isolated nodes and one 2-node component. The project's graph was not touched. Anything I could not confirm is marked "unverified".

## Summary

- Three usable Leiden implementations: `leidenalg` 0.12.0 (GPL-3.0-or-later), `igraph` 1.0.0's `community_leiden` (GPL), and `graspologic-native` 1.3.1 (MIT, Rust). All three ship abi3 wheels for macOS arm64 and Linux x86_64, so CPython 3.11–3.13 is covered. Full `graspologic` 3.4.4 cannot be installed beside this project: it needs `numpy<2.0` and Python `<3.13`, and pulls 39 packages including numba, umap-learn and gensim.
- Only `graspologic-native` has a ready-made hierarchical Leiden. It does not sweep resolutions. It re-runs Leiden inside every cluster larger than `max_cluster_size` until no cluster splits further. `max_cluster_size` is a re-split trigger, not a cap: with a cap of 3, a 7-node cluster survived (ran).
- Fixed seeds reproduce results within one version for all three libraries (ran). Across versions they do not: `graspologic-native` 1.2.5 and 1.3.1 gave different clusters for the same seed (ran). GraphRAG pins `graspologic-native>=1.2,<1.3` for exactly this reason.
- GraphRAG clusters only the relationship edge list. Isolated entities are never clustered, and `use_lcc: true` (the default) keeps only the largest connected component. Edge weights are the LLM's `relationship_strength`, summed over text units; FastGraphRAG uses co-occurrence counts in shared text units, optionally PMI-normalized.
- Community reports are JSON: `title`, `summary`, `rating` (0–10 "impact severity"), `rating_explanation` and 5–10 `findings` with `summary` and `explanation`. Findings cite `[Data: Entities (…); Relationships (…)]` record IDs, not text units. Reports are built from the deepest level up; when a parent's context overflows, sub-community reports replace raw entity and edge text, largest first.
- Global search maps over shuffled batches of reports at one level (CLI default `--community-level 2`). Each map call returns points scored 0–100. The reduce step drops score-0 points, sorts by score and fills a 12,000-token budget. The final answer cites `[Data: Reports (…)]` only. Tracing a sentence to source text takes two hops (report → community → `text_unit_ids`), and nothing checks that the cited report supports the sentence.
- Dynamic community selection rates reports from the root down with a 0–5 relevance prompt and descends only into relevant children. Microsoft reports a 77% token cost cut against static level 1 with no significant quality loss, using GPT-4o-mini as the rater.
- Independent evaluations disagree with the original paper's framing. Han et al. (2025–2026) found community global search 10–15 F1 points below vector RAG on single-hop QA and below it on query-focused summarization against human references. It did beat RAG on MultiHop-RAG's comparison (64.0 vs 57.6) and temporal (53.3 vs 30.7) queries. GraphRAG-Bench found MS-GraphRAG's context relevance far below RAG's (27.3 vs 74.7 on Novel fact retrieval). It also counted about 331,000 prompt tokens per global query against about 900 for vector RAG.
- The original paper's win rates (72–83% comprehensiveness over vector RAG) come from LLM-judged head-to-head comparisons on questions built to exclude "specific low-level facts". Later work found position bias in such judges (Han et al.) and gains "much more moderate than reported previously" once question and judge biases were removed (Zeng et al.).

## 1. Leiden libraries

### 1.1 Versions, licences, wheels and dependencies

| Package | Version (upload date) | Licence | Wheels | Runtime deps |
|---|---|---|---|---|
| `leidenalg` | 0.12.0 (2026-05-24) | GPL-3.0-or-later | `cp38-abi3`: macOS arm64 and x86_64, manylinux x86_64/aarch64, musllinux, Windows | `igraph>=1.0,<2.0` (which needs `texttable`) |
| `igraph` | 1.0.0 (2025-10-23) | GPL (repo: GPL-2.0) | `cp39-abi3`: macOS arm64/x86_64, manylinux_2_28 x86_64/aarch64, musllinux, Windows | `texttable` |
| `graspologic-native` | 1.3.1 (2026-06-14); 1.2.5 (2025-04-02) | MIT | `cp39-abi3` (1.2.5: `cp38-abi3`): macOS universal2, manylinux2014 x86_64/aarch64, musllinux, Windows | 1.3.x: `numpy>=1.24`, `scipy>=1.10`; 1.2.5: none |
| `graspologic` | 3.4.4 (2025-09-08) | MIT | `py3-none-any` | 17 direct; 39 resolved, including numba, llvmlite, umap-learn, gensim, POT, statsmodels, matplotlib, seaborn |

- Source: PyPI JSON for each package (https://pypi.org/pypi/leidenalg/json, https://pypi.org/pypi/igraph/json, https://pypi.org/pypi/graspologic-native/json, https://pypi.org/pypi/graspologic/json). Repository licences from the GitHub API: `vtraag/leidenalg` GPL-3.0, `igraph/python-igraph` GPL-2.0, `graspologic-org/graspologic-native` MIT.
- `graspologic` 3.4.4 declares `requires_python <3.13,>=3.9` and `numpy<2.0.0,>=1.26.4` (PyPI). `uv pip compile` with `graspologic==3.4.4` and `numpy>=2.4` reports the requirements as unsatisfiable (ran). This project locks numpy 2.4.6 on 3.11 and 2.5.3 on 3.12+ (`uv.lock`), so `graspologic` is out. Its last non-dev release is 3.4.4; a `3.4.5.dev2` appeared on 2025-11-25 (PyPI).
- Maintenance: `vtraag/leidenalg` last pushed 2026-10-13, `igraph/python-igraph` 2026-10-06, `graspologic-native` 2026-06-14; none is archived (GitHub API). The GitHub repos `microsoft/graspologic` and `microsoft/graspologic-native` now redirect to the `graspologic-org` organization (GitHub API).
- GPL note: leidenalg and igraph are GPL. Importing a GPL library from the project's Python code raises a licence question if FilingIntel is ever distributed under a non-GPL licence. I have not checked the project's own licence or taken a legal view; this is a flag, not a finding.
- networkx 3.6.1 (already locked, through ragas) has `nx.community.leiden_communities`, but it is declared `implemented_by_nx=False`: "They may only be run with an installable backend" (`networkx/algorithms/community/leiden.py` at `networkx-3.6.1`). Pure networkx cannot run Leiden. Its `louvain_communities` is not Leiden.

### 1.2 Hierarchy, resolution and quality functions

- `leidenalg.find_partition(graph, partition_type, initial_membership=None, weights=None, n_iterations=2, max_comm_size=0, seed=None, **kwargs)` returns one partition (https://leidenalg.readthedocs.io/en/stable/reference.html). There is no hierarchy output. Levels would be built by hand: run once, then run again inside each community, or run at two resolutions. `Optimiser.resolution_profile()` returns partitions across resolutions, which is a different thing (same page).
- leidenalg quality functions (same page): `ModularityVertexPartition` (no resolution parameter, positive weights only), `RBConfigurationVertexPartition` (modularity with `resolution_parameter`, default 1.0), `RBERVertexPartition`, `CPMVertexPartition` ("well-defined for both positive and negative edge weights", takes `resolution_parameter` and `node_sizes`), `SignificanceVertexPartition` (unweighted only) and `SurpriseVertexPartition`. All but Significance take `weights`.
- `max_comm_size` is documented as "Maximal total size of nodes in a community. If zero (the default), then communities can be of any size" (reference page). In my runs it had no effect: with `max_comm_size=5`, modularity, RBConfiguration and CPM partitions of the karate graph still had communities of 12 and 17 nodes (ran, 0.12.0). I did not find out why; treat the cap as unverified.
- leidenalg 0.12.0's changelog entry is "Update to libleidenalg 0.13.0 — Brings support for minimum community size" (`CHANGELOG` on `main`).
- `igraph.Graph.community_leiden(objective_function="CPM", weights=None, resolution=1.0, beta=0.01, initial_membership=None, n_iterations=2, node_weights=None, …)` (`src/igraph/community.py` at `1.0.0`). Only "CPM" and "modularity" are accepted. The default is CPM at resolution 1.0, which on an unweighted graph put every node in its own community: 41 communities for 41 nodes (ran). Modularity must be asked for by name. "Higher resolutions lead to more smaller communities" (docstring).
- `graspologic_native.hierarchical_leiden(edges, starting_communities, resolution, randomness, iterations, use_modularity, max_cluster_size, seed)` takes a list of `(source, target, weight)` string/float tuples (`packages/pyo3/graspologic_native.pyi`). Each output row has `node`, `cluster`, `level`, `parent_cluster` and `is_final_cluster` (as read by GraphRAG's `graphs/hierarchical_leiden.py` at `v3.3.0`).
- How the hierarchy is built (`packages/network_partitions/src/leiden/hierarchical.rs`): one Leiden run gives level 0. Every cluster larger than `max_cluster_size` is cut out as a subnetwork and clustered again with the same resolution; the result is level 1, and so on. A subnetwork whose Leiden run yields one cluster "couldn't break this cluster down any further" and is left as it is. Resolution stays fixed; levels come from recursion on size.
- On the karate graph with `max_cluster_size=10`, there were 5 clusters at level 0, 5 at level 1, and final clusters of 8, 7, 6, 5, 4, 2, 2, 2 nodes (ran, 1.2.5). With `max_cluster_size=3`, a 7-node final cluster remained under 1.3.1 and two 4-node clusters under 1.2.5 (ran). Sizes above the limit are allowed when a cluster cannot be split.
- GraphRAG calls it with `resolution=1.0`, `randomness=0.001`, `use_modularity=True`, `iterations=1`, `starting_communities=None` and its `max_cluster_size` and seed settings (`graphs/hierarchical_leiden.py` at `v3.3.0`).

### 1.3 Determinism

- leidenalg: "By default uses a random seed if nothing is specified" (reference page). Two `find_partition` runs with `seed=42` gave identical memberships (ran). The docs make no promise across versions.
- igraph's `community_leiden` has no seed argument. It draws from Python's `random` module by default: `random.seed(7)` before each call gave identical memberships (ran). Any other use of `random` between calls changes the result, so a run should seed immediately before the call.
- graspologic-native: same seed, same version gave identical output (ran, both versions). Same seed across 1.2.5 and 1.3.1 gave different final cluster sizes ([8, 7, 6, 5, 4, 2, 2, 2] vs [7, 6, 6, 6, 5, 2, 2, 2], ran). GraphRAG's `pyproject.toml` holds `graspologic-native>=1.2,<1.3` with the comment that 1.3.x "changes Leiden clustering output (community counts/levels), which breaks pinned regression tests and the golden community data" (`packages/graphrag/pyproject.toml` at `v3.3.0`).
- GraphRAG also sorts the edge list and normalizes edge direction before clustering, so row order cannot change the result (`index/operations/cluster_graph.py`, `graphs/stable_lcc.py` at `v3.3.0`). Its default seed is `0xDEADBEEF` (`config/defaults.py`).

## 2. Microsoft GraphRAG (v3.3.0)

### 2.1 What gets clustered

- `create_communities` reads the relationships table only and passes it to `cluster_graph(edges, max_cluster_size, use_lcc, seed)` (`index/workflows/create_communities.py` at `v3.3.0`). Edges are made undirected and de-duplicated, keeping the last row (`index/operations/cluster_graph.py`).
- Defaults: `max_cluster_size: 10`, `use_lcc: True`, `seed: 0xDEADBEEF` (`config/defaults.py`, `ClusterGraphDefaults`).
- `stable_lcc` upper-cases and HTML-unescapes names, keeps only edges inside the largest connected component, and sorts them (`graphs/stable_lcc.py`).
- Weights, standard method: the extraction prompt asks for a `relationship_strength` "numeric score" per relationship (`prompts/index/extract_graph.py`). The parser reads it as a float, defaulting to 1.0 (`index/operations/extract_graph/graph_extractor.py`), and duplicate (source, target) pairs are merged with `weight=("weight", "sum")` and their `text_unit_ids` collected (`index/operations/extract_graph/extract_graph.py`).
- Weights, FastGraphRAG: nodes are noun phrases, and an edge joins two phrases that occur in the same text unit, with `weight = len(tu_ids)`, optionally replaced by PMI weights (`index/operations/build_noun_graph/build_noun_graph.py`). The docs recommend "much smaller chunks (50-100 tokens)" for a better co-occurrence graph (https://microsoft.github.io/graphrag/index/methods/).
- A community records `entity_ids`, `relationship_ids` and `text_unit_ids`, but only relationships with both ends inside the community count, and the text units are those relationships' units (`create_communities.py`). Every level is stored with `parent` and `children`, so the tree can be walked both ways (`data_model/community.py`).

### 2.2 Community reports

- Prompt: `COMMUNITY_REPORT_PROMPT` (`prompts/index/community_report.py`). The model reads tables of entities and relationships (and optional claims) with `human_readable_id` columns and returns `title`, `summary`, `rating` ("a float score between 0-10 that represents the severity of IMPACT"), `rating_explanation` and `findings`, "5-10 key insights", each with `summary` and `explanation`. The call uses a Pydantic `response_format` with these fields (`community_reports_extractor.py`).
- Grounding rule: "Points supported by data should list their data references as follows: [Data: <dataset name> (record ids); …]", at most 5 IDs then "+more", and "Do not include information where the supporting evidence for it is not provided" (same prompt). The IDs are entity, relationship, claim or report IDs. Nothing in the pipeline checks them.
- The text-unit variant used by FastGraphRAG (`prompts/index/community_report_text_units.py`) cites `[Data: Sources (…)]`, which are text units.
- Defaults: `max_length: 2000` (words in the report), `max_input_length: 8000` tokens (`CommunityReportDefaults`).
- Leaf context: edges are sorted by combined degree, descending, and added with their endpoint entities until the token limit (`graph_context/sort_context.py`).
- Higher levels: levels are processed deepest first (`summarize_communities/utils.py`, `get_levels` sorts in reverse). For each community, if its own entities and edges fit, they are used. Otherwise "iteratively replace local context with sub-community reports, starting from the biggest sub-community" until it fits; if even all sub-reports overflow, sub-reports are added until the window is full (`graph_context/context_builder.py`, `build_mixed_context.py`). The original paper describes the same rule (arXiv 2404.16130, §3.1.5).

### 2.3 Global search

- Reports for one level are loaded: `--community-level` defaults to 2, "Higher values represent smaller communities" (`cli/main.py`). Without dynamic selection, each entity contributes the deepest community at or below that level (`query/indexer_adapters.py`, `read_indexer_reports`).
- Context: reports are shuffled with `random_state=86`, given an "occurrence weight" (count of text units of the community's entities, normalized) and the report rank, and cut into batches of `max_context_tokens` (`query/context_builder/community_context.py`). The factory sets `shuffle_data: True`, `include_community_rank: True`, `min_community_rank: 0` and uses full report text, not the summary (`query/factory.py`).
- Map: one call per batch. The prompt asks for JSON `{"points": [{"description": "… [Data: Reports (report ids)]", "score": 0-100}]}`, where "An 'I don't know' type of response should have a score of 0" (`prompts/query/global_search_map_system_prompt.py`). `map_max_length` 1,000 words.
- Reduce: all points are pooled, points with score 0 are dropped, the rest sorted by score and added until `max_data_tokens` (`query/structured_search/global_search/search.py`). If every point scored 0 and general knowledge is off, it returns a fixed "I do not know" answer (`NO_DATA_ANSWER`). The reduce prompt keeps "all the data references" and cites `[Data: Reports (…)]` (`global_search_reduce_system_prompt.py`). `reduce_max_length` 2,000 words.
- Budgets: `max_context_tokens: 12_000`, `data_max_tokens: 12_000`, concurrency 32 by default (`GlobalSearchDefaults`; `concurrent_coroutines` in `search.py`).
- The docs: "Lower hierarchy levels, with their detailed reports, tend to yield more thorough responses", at more time and LLM cost (https://microsoft.github.io/graphrag/query/global_search/).
- Citations: the final answer cites report IDs only. A report links to its community, which lists `text_unit_ids`, so source text is reachable in two hops but never cited directly, and no step checks that a report supports the sentence citing it (inference from the prompts and data model above).

### 2.4 Dynamic community selection

- `DynamicCommunitySelection.select` starts from all level-0 reports, rates each with `RATE_QUERY` (JSON `reason`, `rating` 0–5), keeps those at or above `threshold`, queues their children, and by default drops a parent once a child is kept (`keep_parent=False`). If nothing at a level is relevant, it rates every report at the next level, up to `max_level` (`query/context_builder/dynamic_community_selection.py`, `rate_prompt.py`).
- Defaults: `dynamic_search_threshold: 1`, `keep_parent: False`, `num_repeats: 1`, `use_summary: False`, `max_level: 2` (`GlobalSearchDefaults`). In v3.3.0 the rater uses the same model as map-reduce; a `TODO` in `query/factory.py` notes that a separate cheap model is not wired yet.
- Microsoft's evaluation (https://www.microsoft.com/en-us/research/blog/graphrag-improving-global-search-via-dynamic-community-selection/): AP News, 50 global questions, GPT-4o-mini as rater and GPT-4o for map-reduce. Static level 1 maps about 1,500 reports; dynamic selection kept 470 on average. Dynamic at level 1 cut total token cost by 77%, with win rates of 49.5% comprehensiveness, 46.0% diversity and 48.0% empowerment, which the post calls not significant. Dynamic down to level 3 cost 34% more than static level 1 and won 58.8% on comprehensiveness and 60.0% on empowerment.

### 2.5 DRIFT and LazyGraphRAG

- DRIFT starts from "the top K most semantically relevant community reports" to write a first answer and follow-up questions, then answers those with local search (https://microsoft.github.io/graphrag/query/drift_search/). The page gives no evaluation numbers.
- LazyGraphRAG (https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/) builds a noun-phrase co-occurrence graph and community hierarchy with no LLM at index time ("defers all LLM use until query time"), at "0.1% of the costs of full GraphRAG". At query time it ranks chunks by embedding, ranks communities by their best chunks, has a cheap LLM test chunk relevance within a budget, and extracts claims from relevant chunks. On 5,590 AP articles and 100 synthetic queries (50 local, 50 global) it matched global search on global queries at "more than 700 times lower query cost". Win rates are only in a figure. The post does not say whether answers cite chunks.

## 3. Published evaluations

### 3.1 The original paper (Edge et al., arXiv 2404.16130)

- Data: podcast transcripts (1,669 chunks of 600 tokens, about 1 million tokens) and news articles (3,197 chunks, about 1.7 million tokens) (§4.1.1).
- Questions: an LLM generated personas, tasks and questions, 125 per dataset; questions had to need the whole corpus and not "retrieval of specific low-level facts" (§3.2).
- Conditions: C0–C3 are global search over root to low-level reports; TS is map-reduce over raw chunks; SS is vector RAG; all use an 8k context window (§4.1.2, Appendix C).
- Metrics: comprehensiveness, diversity, empowerment and directness (a control), judged head to head by an LLM, five replicates (§3.3, §4.1.3).
- Results against SS (vector RAG): comprehensiveness win rates of 72–83% (podcast) and 72–80% (news); diversity 75–82% and 62–71%. Vector RAG wins on directness. Empowerment results were mixed (§5.1).
- Tokens: C0 used about 2.3–2.6% of TS's tokens; C3 used 26–33% fewer than TS (§5.1, Table 2).
- A claim-based check (Claimify, 47,075 claims) agreed: global methods and TS had more claims than SS (p < .05). Where the LLM judge did not call a tie, it matched the claim-based label 78% of the time on comprehensiveness (§4.2, §5.2).
- Stated limits: sensemaking questions only, two corpora of about 1M tokens, fabrication not measured (§6.1).

### 3.2 Han et al., "RAG vs. GraphRAG: A Systematic Evaluation and Key Insights" (arXiv 2502.11371, v3 2026-03-04)

- Single-hop and detail QA, F1: on NQ, Community-GraphRAG (Global) 54.48 vs RAG 64.78; on HotpotQA, 45.16 vs 60.04 (Table 1). On NovelQA detail questions, 30.89 vs 55.28 (Table 3). The authors: "Global search retrieves high-level community summaries, which can lose fine-grained evidence and hurt detail-centric QA" (§4.2).
- MultiHop-RAG (Table 2): global beat RAG on comparison (64.02 vs 57.59) and temporal (53.34 vs 30.70) queries, lost on inference (89.34 vs 92.16), and collapsed on null queries (19.27 vs 96.01), which the authors tie to hallucination.
- KG coverage: "only about 65.8% of answer entities appear in the constructed KG for HotPotQA and 65.5% for NQ" (§4.2, Appendix C).
- Query-focused summarization against human references (ROUGE-2 F1): global search was below RAG on all four datasets, e.g. QMSum 3.23 vs 6.32 (Tables 6–7).
- LLM-judge position bias: swapping the order of the two answers gave "substantially different, and in some cases opposite, judgments" (§5.3).
- Conclusion: "RAG and GraphRAG exhibit complementary behaviors rather than a consistent winner" (§1). On MultiHop-RAG, 13.6% of queries were answered only by GraphRAG and 11.6% only by RAG (§4.4).

### 3.3 GraphRAG-Bench (Xiang et al., arXiv 2506.05690, v3 2026-02-22)

- Tasks: fact retrieval, complex reasoning, contextual summarization, creative generation, on a novels corpus and a medical-guidelines corpus (Table 1, §3.2).
- Accuracy, Novel: MS-GraphRAG 49.29 vs RAG 58.76 on fact retrieval; 50.93 vs 41.35 on complex reasoning; 64.40 vs 50.08 on summarization. Medical: MS-GraphRAG below RAG on every task (Table 2, GPT-4o-mini). The table does not split local and global search.
- Retrieval (Table 3), evidence recall / context relevance: on Novel fact retrieval, MS-GraphRAG 61.04 / 27.30 vs RAG 61.37 / 74.66. MS-GraphRAG recall was higher on Novel reasoning (73.03 vs 59.80) and summarization (82.02 vs 69.08), but relevance stayed far lower. On Medical, relevance fell to 2.8–5.7.
- Prompt tokens per query (Figure 8): about 880–950 for vector RAG, about 39,000 for MS-GraphRAG local and about 331,000 for global. The text gives smaller figures elsewhere (Obs. 8–9 mention about 40,000), so the page is internally inconsistent on this number.

### 3.4 Zeng et al., "How Significant Are the Real Performance Gains?" (arXiv 2506.06331, v2 2026-08-13)

- The abstract names two flaws in the usual GraphRAG protocol, "unrelated questions and evaluation biases", proposes graph-text-grounded question generation and an unbiased judging procedure, and finds the gains of three GraphRAG methods "much more moderate than reported previously". I read the abstract only; the numbers are unverified here.

### 3.5 What the evidence says about FilingIntel's question type

- Corpus-wide sensemaking ("main themes") is where global search wins, and the original evaluation excluded fact questions by design (3.1).
- Questions that need specific evidence per entity lose with community summaries: detail QA, single-hop QA and fact retrieval all favour vector RAG (3.2, 3.3).
- Comparison and temporal multi-hop questions are the exception where community global search beat RAG (3.2). FilingIntel's global questions are per-company comparisons, so they sit between the two cases.
- Retrieval relevance is consistently much lower for MS-GraphRAG even when evidence recall is similar or higher (3.3). A metric on chunk IDs will show this directly.

## 4. Sparse graphs and isolated nodes

- Leiden with modularity gives an isolated node nothing to gain by joining any community, so it stays alone. leidenalg put each of 5 isolated nodes in its own community and the 2-node component in one shared community (ran).
- graspologic-native takes an edge list, so a node with no edges cannot be passed at all: 36 of 41 nodes came back (ran). GraphRAG inherits this and, with `use_lcc: True`, also drops every smaller component (2.1).
- GraphRAG's FastGraphRAG pipeline adds a `prune_graph` step before clustering, with defaults `min_node_freq: 2`, `min_node_degree: 1`, `min_edge_weight_pct: 40.0`, `remove_ego_nodes: True` and `lcc_only: False` (`config/defaults.py`, `PruneGraphDefaults`). `remove_ego_nodes` removes the single highest-degree node (`index/operations/prune_graph.py`). The standard pipeline has no prune step (`index/workflows/factory.py`).
- Co-occurrence edges from shared text units are the documented way to connect a sparse graph: FastGraphRAG uses them as the only edges (2.1), and LazyGraphRAG builds its whole community hierarchy from them (2.5).
- igraph's default CPM at resolution 1.0 returns all singletons on an unweighted graph (1.2), a trap for anyone switching libraries.

## 5. Cost

- Indexing: the docs warn that "GraphRAG can consume a lot of LLM resources" (`docs/get_started.md` at `v3.3.0`). Microsoft estimates graph extraction at "roughly 75% of indexing cost" (https://microsoft.github.io/graphrag/index/methods/). FilingIntel has already paid for extraction (Steps 8–10), so Step 12 pays only for reports.
- Reports: one LLM call per community with up to 8,000 input tokens and a 2,000-word report (2.2). Arithmetic with `retrieve/pricing.py` (table 2026-10-10), assuming 8,000 input and 2,700 output tokens per report, with no prompt caching: claude-haiku-5-5 ($0.10 / $0.50 per MTok) about $0.0021 per report, so about $1.10 for 500 reports; claude-sonnet-5-5 ($2.00 / $10.00) about $0.043 per report, so about $21 for 500. The community count is unknown until clustering runs.
- Global query: one map call per 12,000-token batch of reports plus one reduce call (2.3). Arithmetic for 300,000 tokens of reports at one level: 25 map calls of 12,000 input and about 1,300 output tokens cost about $0.93 per question on Sonnet 5.5 and about $0.05 on Haiku 5.5, before the reduce call.
- Published per-query counts: about 331,000 prompt tokens per MS-GraphRAG global query vs about 900 for vector RAG in GraphRAG-Bench (3.3, internally inconsistent); C0 root-level reports at 2.3–2.6% of map-reduce-over-chunks tokens in the original paper (3.1).
- Capping fan-out, as published: choose a higher (coarser) level (docs, 2.3); dynamic selection with a cheap rater, minus 77% at level 1 (2.4); token budgets on map batches and the reduce step (2.3); and `min_community_rank` to drop low-rated reports (2.3, default 0). LazyGraphRAG caps work with a relevance-test budget (2.5).
- voyage-4-large is $0.12 per MTok (`retrieve/pricing.py`), so embedding even 1,000 summaries of 500 tokens costs about $0.06.

## 6. What this means for FilingIntel

These follow from the facts above and the project's settled rules. They are recommendations, not decisions.

1. Library: use `graspologic-native`, pinned exactly (`==1.3.1`, or `==1.2.5` to match GraphRAG's golden data). It is MIT, has the hierarchy built in, needs only numpy and scipy (already locked), and is reproducible with a fixed seed within a version (1.1, 1.3). `graspologic` cannot coexist with numpy 2 (1.1). `leidenalg` is the fallback if CPM or an explicit second resolution is wanted, at the cost of a GPL dependency and hand-built levels. Either way, record library, version, seed, resolution, `max_cluster_size` and an edge-list hash in the run report, and treat a version bump as a new clustering.
2. Two levels: graspologic-native's levels come from re-splitting by size, not from resolution (1.2). For exactly two levels, keep levels 0 and 1 and merge anything deeper into level 1, or run Leiden twice by hand. Record which, since the RUNBOOK says "two levels". `max_cluster_size` is not a hard cap, so report actual sizes.
3. Which graph to cluster: cluster extracted nodes only. Keep `Filing`, `Period` and `Chunk` out, or communities will follow filings rather than topics. Edge weight = number of distinct evidence chunks on the extracted edge, the analogue of GraphRAG summing per-text-unit weights (2.1).
4. Hubs: the three `Company` nodes touch most of the graph (`MEASURES`, `OFFERS`, `HAS_SEGMENT`, `COMPETES_WITH`, …). Global questions compare the three firms on one topic, so topic communities that span firms (TSMC with all three) serve them better than one community per firm. Options: drop `Company` from clustering, as FastGraphRAG's `remove_ego_nodes` drops the top hub (4), then attach each community to the firms its members' evidence comes from; or keep them and check whether communities split by firm. Measure both on the 10 dev global questions' gold chunks before freezing.
5. Sparse nodes: most `RiskFactor` nodes connect only through `PERSISTS_AS` chains or `AFFECTS`, and `MetricValue` nodes hang off one `MEASURES` edge each (counts in the step brief). Without extra edges, many will be singletons or tiny chains, and GraphRAG's `use_lcc` would drop them entirely (4). Options: (a) add co-occurrence edges between extracted nodes evidenced by the same chunk, weighted by shared chunk count, as FastGraphRAG and LazyGraphRAG do (4); (b) leave `MetricValue` out of clustering, since global questions are about risk and dependence, not figures; (c) keep singletons as their own level-0 communities but write no report for them. Report how many nodes end up in singletons or in communities below a minimum size, per label.
6. Citations: GraphRAG's answer cites report IDs only (2.3), which fails invariant 3. ADR-0007 already says graph arms hand at most 10 ranked chunk IDs to the unchanged answer prompt. For `graph_global` that suggests: reports whose findings cite FilingIntel chunk IDs (validated against the community's own evidence chunks, as Step 8 checks spans); a map step that returns scored points with chunk IDs; then a ranking of chunk IDs by point score, cut to 10, passed to the existing answer prompt. Report text never reaches the answer model, so every sentence still cites a real retrieved chunk.
7. Report prompt: copy GraphRAG's structure (title, summary, findings with explanations) but replace "impact severity" with a rating that fits filings, or drop it. Feed the model chunk-ID-keyed evidence spans, not free descriptions, since the project's nodes have names and spans, not descriptions (text-to-cypher-and-local-search.md, implication 7). Use structured output, as GraphRAG's `response_format` does (2.2).
8. Fan-out: with probably a few hundred communities, static map-reduce over level 1 on Haiku 5.5 costs cents per question; on Sonnet 5.5 about a dollar (5). Dynamic selection with Haiku as rater is the published cap (2.4). Because questions name companies and periods, the ADR-0001 filter can drop communities with no evidence in the filtered filings before any call, a deterministic cap that costs nothing.
9. Expectations: the literature predicts global search loses on specific evidence and wins sometimes on comparisons (3.5). The global class has 10 dev questions with 3–4 gold chunks each and the vector arm at recall@10 0.800, so any difference is "directional" under the research plan. Report recall@5/10, wrong-evidence rate and the judged scores per class against `vector` with the paired bootstrap from Step 11; do not use head-to-head LLM win rates, which have documented position bias (3.2).
10. Summary embeddings: embed report text with voyage-4-large through the existing cached path; the cost is negligible (5). Their use is ranking communities against the question (DRIFT's "top K most semantically relevant community reports", 2.5), which could replace or pre-filter the LLM rater.

## 7. Open questions

1. How many nodes does each label leave as singletons under the extracted-edge graph, with and without shared-chunk co-occurrence edges? This decides option 5.
2. Should `Company` nodes be in the clustered graph (option 4)? Needs a measurement on the dev global questions' gold chunks.
3. Should `MetricValue` (3,779 nodes) be clustered at all, or left to the lookup and local arms?
4. Which `graspologic-native` version to pin: 1.3.1 (current) or 1.2.5 (GraphRAG's)? No golden data depends on either yet.
5. Is `max_cluster_size` 10 right for 2,000-odd nodes, and how are levels beyond 1 folded into "two levels"?
6. Which model writes reports: Haiku 5.5 (about $1 for 500 reports) or Sonnet 5.5 (about $21)? Report accuracy is not otherwise measured; a small review round like Step 8's would need the user.
7. Does the map step choose chunks (option 6), or should `graph_global` rank communities by summary embedding and return their best evidence chunks with no map LLM call at all? The second is cheaper and deterministic but is no longer GraphRAG global search.
8. Why did leidenalg 0.12.0's `max_comm_size` not cap community size in my runs (1.2)? Only matters if leidenalg is chosen.
9. Is a GPL dependency acceptable for this repository, given its licence (1.1)?
10. Zeng et al.'s corrected numbers (3.4) were not read beyond the abstract.
