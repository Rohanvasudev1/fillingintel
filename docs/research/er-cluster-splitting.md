# Splitting resolution groups that hold contradictions (Step 9b)

Research note for Step 9b, written 2026-10-12, before the grilling settles the split rule. Step 9 runs union-find over `same` decisions and holds back a whole group when it holds a `related` or `distinct` pair or has more than 5 names (`resolve/groups.py`, `MAX_GROUP_NAMES = 5`). Step 9b splits such a group so no `related` or `distinct` pair shares a piece, then merges each piece (RUNBOOK Step 9b). This note covers how ER libraries turn pairwise decisions into clusters, how they guard against chaining and oversized clusters, what theory says about splitting with cannot-link pairs, and what LLM-based ER papers do. It does not repeat `entity-resolution.md` section 3 (union-find, the pivot algorithm, the first contradiction rule).

Sources: source code read with `gh api` (dedupe at commit 3f61e79, Splink tag v5.0.0, pyJedAI at commit eb44609, Zingg at commit 9e3595a); Splink and Zingg docs from their repos; papers' own PDFs or publisher pages. All read on 2026-10-12. Claims marked "(ran)" come from scripts in the session scratchpad, run read-only against the committed `benchmarks/resolution/run2.json`; no API was called. "Contradiction group" below means a held-back group with at least one `related` or `distinct` pair; "size-only group" means one held back only for having more than 5 names.

## Summary

- **No mainstream ER library splits a connected component on cannot-link pairs.** dedupe, Splink, Zingg and pyJedAI's connected-components method all take the transitive closure of above-threshold pairs. They control chaining in other ways: dedupe re-runs hierarchical clustering inside each component; Splink offers a constrained greedy alternative and bridge/density metrics for review; Zingg reports the weakest link per cluster for review. None treats a negative pairwise answer as a hard constraint (sections 1–4).
- **Every library's guard depends on a numeric score.** dedupe raises the threshold inside an oversized component; Splink ranks edges by match probability; pyJedAI's methods sort by similarity. We have only categorical answers, so each of these collapses to ties or to an arbitrary order (sections 1–4).
- **Hard size limits are rare and large.** dedupe's `max_components` defaults to 30,000 and triggers re-filtering, not rejection. Splink and Zingg say what size is suspicious depends on the dataset and point users to inspection, with Zingg suggesting clusters of 4 or 5+ be inspected. Density and weakest-link checks are what the docs recommend (sections 1, 2, 4).
- **Theory.** Splitting a group so every cannot-link pair is separated while cutting the fewest `same` edges is minimum multicut. With +/−/0 labels it is correlation clustering on a general graph, which Demaine et al. show is equivalent to multicut and APX-hard. At our sizes neither matters: exact enumeration of every partition takes under 0.6 s per group (sections 5–6, ran).
- **Constrained union-find is order-dependent on our data.** Merging `same` pairs in decision-ID order and refusing any merge that joins a cannot-link pair gives a different partition when the order is reversed in 11 of 12 contradiction groups, and often cuts more `same` edges than needed (7 vs an optimum of 3 in one group) (section 7, ran).
- **Exact minimum multicut has a unique optimum in 9 of 12 contradiction groups.** The 3 with ties are the small ones (3–4 names) (section 6, ran).
- **LLM ER papers cluster LLM output but do not publish a cannot-link repair rule.** Fu et al. (SIGMOD 2026) cap the record set at 9 and add a misclustering guardrail that regenerates the set; a 2026 practice paper warns that transitivity under a low-precision matcher builds mega-clusters and calls for re-verifying cross-group merges (section 8).

## 0. The held-back groups in run 2 (ran)

`run2.json` holds 21 held-back groups: 12 contradiction groups and 9 size-only groups. Sizes run from 3 to 11 names and up to 22 `same` edges. Size-only groups have `same`-edge density (edges over all pairs) of 0.33 to 0.79; contradiction groups 0.33 to 0.83. Each contradiction group has 1 to 5 `related`/`distinct` pairs and 0 to 14 `unsure` pairs.

## 1. dedupe

- `dedupe.clustering.cluster(dupes, threshold=0.5, max_components=30000)` first splits the scored pairs into connected components with union-find, then runs SciPy hierarchical clustering with `method="centroid"` inside each component and cuts the dendrogram at distance `1 - threshold` (https://github.com/dedupeio/dedupe/blob/3f61e79102910bd355e920a2df7e44c14c9cb247/dedupe/clustering.py).
- Unscored pairs inside a component get distance 1: `condensedDistance` fills the matrix with ones and writes `1 - score` only for scored pairs (same file). So a missing pair counts as "different" during the hierarchical step, which is how dedupe breaks chains: A–B and B–C with no A–C score is not a tight cluster.
- Oversized components are re-filtered, not rejected. When a component has more than `max_components` records, `_connected_components` logs a warning, raises the threshold by one logit unit above the component's lowest score, drops edges below it and recurses (same file). It never stops a component outright.
- Order and ties: union-find sorts edges by label and score with a stable sort; the hierarchical step is deterministic given the distances. Cost: one linkage per component, trivial at our sizes.
- dedupe's two-dataset modes (`greedyMatching`, `gazetteMatching`) take pairs in descending score and accept a pair only if neither record is already matched (same file). These are one-to-one constraints, not cannot-link pairs.
- Fit: needs a score. With categorical answers every `same` edge has the same distance, so centroid linkage at any threshold either keeps the whole component or splits it into singletons.

## 2. Splink

- `cluster_pairwise_predictions_at_threshold` is connected components over pairs at or above a probability or weight threshold (https://github.com/moj-analytical-services/splink/blob/v5.0.0/splink/internals/linker_components/clustering.py). There is no splitting step.
- `cluster_using_single_best_links` is Splink's constrained alternative. It joins a record into a cluster only if the link is mutually the best for the record and the cluster, and only if the join keeps at most one record from each dataset listed in `duplicate_free_datasets` (same file, docstring). The SQL in `one_to_one_clustering.py` iterates: each round it ranks candidate links per cluster by `match_probability desc, node_id`, keeps links ranked first on both sides, and excludes any link where both clusters already hold a record from the same duplicate-free dataset (https://github.com/moj-analytical-services/splink/blob/v5.0.0/splink/internals/one_to_one_clustering.py). This is a greedy constrained merge, the nearest thing in a major library to "refuse a merge that would join a cannot-link pair", but its constraint is "two records from one source", not an arbitrary pair.
- Ties are handled explicitly: `ties_method="lowest_id"` keeps the link to the lowest ID; `"drop"` drops all tied links into a duplicate-free dataset (clustering.py docstring and `drop_ties_sqls` in one_to_one_clustering.py). `drop` is the "leave ambiguous keys unmerged" option.
- Bad-cluster metrics: node degree and centrality, edge `is_bridge`, and cluster size, density and centralisation (https://github.com/moj-analytical-services/splink/blob/v5.0.0/docs/topic_guides/evaluation/clusters/graph_metrics.md). Bridges are computed with igraph (`_compute_metrics_edges` in clustering.py). The guide calls a bridge a possible sign of a false positive, "especially when joining two highly connected sub-clusters"; it says a large, low-density cluster is more suspicious than a large dense one; it says graph metrics are "rarely definitive" and that the right size cap depends on the dataset (same doc). These metrics feed review in Cluster Studio; Splink does not cut bridges automatically.
- Fit: the bridge and density metrics apply to us directly (no score needed). The single-best-links ranking needs a score; with categorical answers it reduces to ranking by node ID.

## 3. pyJedAI (JedAI in Python)

All in `src/pyjedai/clustering.py` (https://github.com/AI-team-UoA/pyJedAI/blob/eb446095f921af3207bbefd570cc65e818194419/src/pyjedai/clustering.py). Methods follow Hassanzadeh et al., "Framework for Evaluating Clustering Algorithms in Duplicate Detection", PVLDB 2(1), 2009 (http://www.vldb.org/pvldb/2/vldb09-1025.pdf; paper not read here).

| Method | What it does | Exact or greedy; order | Notes for us |
|---|---|---|---|
| ConnectedComponentsClustering | Drops edges below a threshold, returns connected components | Exact | Same as our union-find |
| UniqueMappingClustering | Sorts edges by weight, accepts a pair only if neither entity is already matched | Greedy; ties follow the priority queue's tuple order | Raises an error for dirty (single-source) ER; two-source only |
| CenterClustering | Edges by descending weight; the endpoint with the higher mean edge weight becomes a center, the other a member; a member never joins a second cluster | Greedy, order by weight | Needs weights to pick centers |
| MergeCenterClustering | As Center, but the left-dataset entity is always the center | Greedy | Two-source |
| CorrelationClustering | Starts from components above `initial_threshold`; marks pairs above `similarity_threshold` as similar and below `non_similarity_threshold` as not similar, pairs between as neither; runs 100 random moves (move, merge, split) with a fixed seed, keeping moves that raise agreements | Local search, seeded | The three-way similar / not-similar / neither labelling matches our same / related-or-distinct / unsure |
| CutClustering | Adds a sink joined to every node with capacity `alpha`, builds a Gomory–Hu tree, returns its components | Exact given weights | Needs weights; `alpha` sets how readily it cuts |
| MarkovClustering | Expansion and inflation on the weighted adjacency matrix, then components above a threshold | Iterative | Needs weights |

None of them takes a cannot-link list. CorrelationClustering is the only one that uses negative evidence, and it treats it as soft.

## 4. Zingg and Magellan

- Zingg clusters with GraphFrames `connectedComponents()` over matched pairs (https://github.com/zinggAI/zingg/blob/9e3595a9ca9924b7a522d4eb7904542f3a53ae08/spark/core/src/main/java/zingg/spark/core/util/SparkGraphUtil.java). It does not split clusters. It writes `z_minScore` and `z_maxScore` per cluster, the weakest and strongest link, and its docs say to flag clusters whose minimum is 0 or very low for manual review (https://github.com/zinggAI/zingg/blob/9e3595a9ca9924b7a522d4eb7904542f3a53ae08/docs/interpreting-results/interpret-output-scores.md).
- Zingg's scoring guide says matching is transitive, so records join a cluster at different confidence levels, and suggests inspecting clusters of size 4 or 5 and up (https://github.com/zinggAI/zingg/blob/9e3595a9ca9924b7a522d4eb7904542f3a53ae08/docs/scoring.md). That is a review trigger, not a hard limit.
- py_entitymatching (Magellan) predicts matching pairs across two tables; its repository has no clustering or transitive-closure module (searched the tree at https://github.com/anhaidgroup/py_entitymatching). Not relevant here.

## 5. Theory: correlation clustering and multicut

- Bansal, Blum and Chawla define correlation clustering on a complete graph whose edges are labelled + or −: find the partition that minimizes + edges between clusters plus − edges inside them. The number of clusters is not a parameter. They give a constant-factor approximation for minimizing disagreements and a PTAS for maximizing agreements, and note that if a perfect clustering exists, deleting − edges and taking components finds it (Machine Learning 56, 2004; preprint https://www.cs.cmu.edu/~shuchi/papers/clusteringfull.pdf). They also suggest a classifier's log-odds as the edge weight, with 0 when the classifier is unsure (same paper, introduction), which is the role `unsure` plays for us.
- On general weighted graphs (pairs may be unlabelled), Demaine, Emanuel, Fiat and Immorlica give an O(log n) approximation, show the problem is equivalent to minimum multicut, and so APX-hard (Theoretical Computer Science 361(2–3), 2006; https://erikdemaine.org/papers/Clustering_TCS/).
- What this means for "hard" versus "soft" negatives. If `related`/`distinct` are hard constraints and `same` edges are what we cut, the problem is minimum multicut: cut the fewest `same` edges so every cannot-link pair is disconnected. With one cannot-link pair it is a minimum s–t cut, solvable by max-flow; with several pairs it is NP-hard in general (via the multicut equivalence above). If negatives are soft (weight −1 against +1 for `same`), it is correlation clustering, and a `distinct` pair can end up inside a cluster when separating it would cut more `same` edges.
- Small instances are exact by enumeration. A group of n names has Bell(n) partitions: 115,975 at 10 and 678,570 at 11 (ran). The largest contradiction group in run 2 has 10 names and took 0.57 s in plain Python (ran). An ILP solver would also do, but needs a dependency.

## 6. Exact multicut and correlation clustering on run 2 (ran)

For each of the 12 contradiction groups, the script enumerated every partition, kept those that separate every `related`/`distinct` pair and whose pieces are connected by `same` edges, and minimized the number of cut `same` edges. `unsure` and unjudged pairs count for nothing.

| Group (names, same, cannot) | Min cut | Optimal partitions |
|---|---|---|
| 4, 2, 3 | 1 | 2 |
| 8, 13, 2 | 2 | 1 |
| 9, 15, 5 | 2 | 1 |
| 6, 9, 2 | 2 | 1 |
| 8, 10, 3 | 2 | 1 |
| 6, 8, 1 | 1 | 1 |
| 4, 5, 1 | 2 | 2 |
| 3, 2, 1 | 1 | 2 |
| 7, 12, 1 | 1 | 1 |
| 7, 10, 1 | 1 | 1 |
| 10, 18, 4 | 3 | 1 |
| 4, 4, 1 | 1 | 1 |

- Unique optimum in 9 of 12. In the three tied groups, a "leave ambiguous keys unmerged" rule (merge only pairs that share a piece in every optimal partition) would leave 2, 4 and 2 pairs undecided.
- Soft correlation clustering (+1 `same`, −1 `related`/`distinct`, 0 otherwise) had more than one optimum in 10 of 12 groups, and in 8 of 12 the first optimum found kept a `related` or `distinct` pair inside one cluster. With equal weights, cutting one `same` edge costs the same as violating one cannot-link pair, so ties are the norm.

## 7. Constrained union-find and vertex cuts on run 2 (ran)

- Constrained union-find: take `same` pairs in order, union two components unless some cannot-link pair would end up inside the merged component. This is the COP-KMeans rule applied to merges: Wagstaff et al. never break a constraint and return failure when no legal cluster exists (ICML 2001, https://www.wkiri.com/research/papers/wagstaff-kmeans-01.pdf). Davidson and Ravi show that COP-KMeans is sensitive to the order in which points are assigned (AAAI 2006, https://www.cs.ucdavis.edu/~davidson/Publications/aaai06.pdf) and that deciding feasibility under cannot-link constraints with a bounded number of clusters is NP-complete (SDM 2005, https://web.cs.ucdavis.edu/~davidson/Publications/SDM2005Final.pdf). With the number of clusters unbounded, as for us, singletons are always feasible, so infeasibility does not arise; order dependence does.
- On run 2, decision-ID order (a hash, so arbitrary) and its reverse gave different partitions in 11 of 12 contradiction groups. Against the exact minimum: forward order cut 7 `same` edges where 3 suffice in the 10-name group, 4 where 2 suffice in an 8-name group, and 3 where 2 suffice in a 4-name group; reverse order was also off in several. Any fixed order (decision ID, key name, quote length) is a tie-break with no meaning behind it.
- Minimum vertex cut (drop the fewest keys so every cannot-link pair is disconnected; dropping an endpoint counts): size 1 in 11 of 12 groups, size 2 in one. The minimum vertex cut was unique in only 3 of 12; often any one of the cannot-link endpoints will do, so the rule needs its own tie-break.

## 8. LLM-based entity resolution

- Fu et al., "In-context Clustering-based Entity Resolution with Large Language Models: A Design Space Exploration", SIGMOD 2026 (https://arxiv.org/abs/2506.02509). The LLM clusters a set of records in one call; records in one output cluster are the same entity and clusters from one set are distinct. They set the record set size to 9 with diversity 4, reporting that accuracy falls past about 9 or 10 records. A Misclustering Detection Guardrail flags a record whose similarity to its own cluster is lower than to another cluster, moves it within the set and asks the LLM again; it does not split clusters by rule. Clusters are merged across rounds by a greedy heuristic because exact packing is NP-hard (https://arxiv.org/html/2506.02509).
- Pavani et al., "Entity Resolution in Practice: Lessons from a Self-Serve Pipeline", arXiv:2607.26298, 2026 (https://arxiv.org/abs/2607.26298; abstract only read). The abstract warns that assuming transitivity lets one false link chain many records together and says every cross-group merge must be re-verified. A search summary of the full text reported that transitivity was safe only with high baseline precision; that part was not checked against the paper and is unconfirmed.
- Wang et al. (COLING 2025, https://arxiv.org/abs/2405.16884) and Peeters et al. (EDBT 2025, https://arxiv.org/abs/2310.11244) are covered in `entity-resolution.md` section 6. No paper found applies multicut or correlation clustering to categorical LLM answers with a `related` class; this is a gap, not a finding that it fails.
- The cheap extra guard from `entity-resolution.md` section 3 still applies: before merging a piece, adjudicate any pair inside it that was never judged. Step 9b's RUNBOOK text says the split reuses cached answers and makes no new calls, so this would be a separate decision.

## 9. Size limits in practice

| System | Size handling | Source |
|---|---|---|
| dedupe | `max_components=30000`; above it, raise the threshold and recurse | clustering.py (section 1) |
| Splink | No limit; size, density, bridges for review; "suspicious" depends on the dataset | graph_metrics.md (section 2) |
| Zingg | No limit; inspect clusters of 4–5+ and those with a low `z_minScore` | scoring.md (section 4) |
| Fu et al. | Input set size 9, from measured accuracy | arXiv:2506.02509 (section 8) |

None of them throws away a cluster for size alone. The 5-name limit in Step 9 came from run 1, where no real entity had more than three surface forms in one filing (`entity-resolution.md` section 3). Across 24 filings, size-only groups in run 2 have `same`-edge density of 0.33 to 0.79 (ran), so some are near-cliques that Splink's guide would call unsuspicious. A density or bridge check targets chaining more directly than a name count.

## Implications for FilingIntel Step 9b

This compares the candidate rules; the grilling decides.

| Rule | Exact? | Order or tie dependence on run 2 | Cost at our sizes | Notes |
|---|---|---|---|---|
| Minimum `same`-edge multicut, pieces connected, ties left unmerged | Exact | Unique in 9 of 12; ties in 3 small groups, leaving 2–4 pairs unmerged | Enumeration, under 0.6 s per group; no dependency | Treats `related`/`distinct` as hard. Matches "no related/distinct pair shares a piece" literally. Needs a guard for n beyond about 12 (Bell(12) is 4.2 million). |
| Minimum vertex cut (drop bridge keys) | Exact | Unique in 3 of 12 | Small brute force or networkx (locked) | Removes whole keys from merging; often any cannot-link endpoint qualifies, so it needs a tie-break. Loses more recall than an edge cut when the dropped key has many `same` edges. |
| Constrained union-find in a fixed order | Greedy | Different partition in 11 of 12 when the order is reversed; up to 7 cuts where 3 suffice | Trivial | Closest to Splink's single-best-links and COP-KMeans, but those rank by a score we lack. The result depends on an arbitrary ordering. |
| Correlation clustering (+1/−1/0) | Exact by enumeration | More than one optimum in 10 of 12; the first optimum kept a cannot-link pair together in 8 of 12 | As multicut | Soft negatives break the "no related/distinct pair shares a piece" requirement unless negatives get a large weight, which turns it back into multicut. |

On size: the libraries reviewed use size as a review signal, not a hard cut, and recommend density, bridges or the weakest link instead. If the 5-name limit goes, a replacement guard (density floor, no bridge edges inside a merged piece, or a larger cap) is a separate choice for the grilling. Whatever rule is chosen, the merge log should record each piece, the cut `same` edges and any tie left unmerged, so a split can be replayed and undone (ADR-0006).

## Sources

- dedupe `dedupe/clustering.py` at 3f61e79. https://github.com/dedupeio/dedupe/blob/3f61e79102910bd355e920a2df7e44c14c9cb247/dedupe/clustering.py
- Splink v5.0.0: `splink/internals/linker_components/clustering.py`, `splink/internals/one_to_one_clustering.py`, `docs/topic_guides/evaluation/clusters/graph_metrics.md`. https://github.com/moj-analytical-services/splink/tree/v5.0.0
- pyJedAI `src/pyjedai/clustering.py` at eb44609. https://github.com/AI-team-UoA/pyJedAI/blob/eb446095f921af3207bbefd570cc65e818194419/src/pyjedai/clustering.py
- Zingg at 9e3595a: `SparkGraphUtil.java`, `docs/scoring.md`, `docs/interpreting-results/interpret-output-scores.md`. https://github.com/zinggAI/zingg/tree/9e3595a9ca9924b7a522d4eb7904542f3a53ae08
- py_entitymatching. https://github.com/anhaidgroup/py_entitymatching
- Hassanzadeh, Chiang, Miller, Lee, PVLDB 2(1), 2009 (not read). http://www.vldb.org/pvldb/2/vldb09-1025.pdf
- Bansal, Blum, Chawla, "Correlation Clustering", Machine Learning 56, 2004. https://www.cs.cmu.edu/~shuchi/papers/clusteringfull.pdf
- Demaine, Emanuel, Fiat, Immorlica, "Correlation Clustering in General Weighted Graphs", TCS 361, 2006. https://erikdemaine.org/papers/Clustering_TCS/
- Wagstaff, Cardie, Rogers, Schrödl, "Constrained K-means Clustering with Background Knowledge", ICML 2001. https://www.wkiri.com/research/papers/wagstaff-kmeans-01.pdf
- Davidson, Ravi, "Clustering with Constraints: Feasibility Issues and the k-Means Algorithm", SDM 2005. https://web.cs.ucdavis.edu/~davidson/Publications/SDM2005Final.pdf
- Davidson, Ravi, "Identifying and Generating Easy Sets of Constraints for Clustering", AAAI 2006. https://www.cs.ucdavis.edu/~davidson/Publications/aaai06.pdf
- Fu et al., SIGMOD 2026. https://arxiv.org/abs/2506.02509
- Pavani et al., arXiv:2607.26298 (abstract only). https://arxiv.org/abs/2607.26298
