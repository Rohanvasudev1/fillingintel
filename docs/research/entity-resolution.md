# Entity resolution for extracted graph nodes (Step 9)

Research note for Step 9, written 2026-10-10, before the Step 9 grilling settles anything. Step 9 merges duplicate extracted nodes: authoritative keys first, then normalization, blocking within a label, LLM adjudication with evidence, and an append-only, reversible merge log, scored for precision and recall on about 100 hand-labelled pairs (RUNBOOK Step 9, GRAPH-LAYER.md "Entity resolution"). This note covers how to score it, how to block, how to turn pairwise decisions into clusters, which libraries fit, how to strip legal suffixes, what is published on LLM matching, how three GraphRAG tools handle duplicates, and what SEC's XBRL API holds.

Sources: papers on arXiv, PVLDB and publisher pages; Voyage AI docs (docs.voyageai.com); SEC pages on sec.gov and one live `data.sec.gov` response; PyPI JSON metadata; GitHub source of GraphRAG v3.3.0, LightRAG v1.5.7, neo4j-graphrag-python 1.22.0, LLM Graph Builder v0.8.7 and Splink v5.0.0, read with `gh api`; the GLEIF ELF code list CSV v1.6. All read on 2026-10-10. Claims marked "(ran)" were run in `/private/tmp/claude-501/er-research` with `uv run --no-project --with <pkg>`, never in the project, or against the Step 8 run file `data/extract/0001045810-26-000021-run1.jsonl` in the main checkout (read only). No paid API was called and no Voyage embeddings were computed, so every embedding threshold below is a thing to measure, not a finding. Anything without a primary source is marked "unconfirmed". This note does not repeat `claude-extraction-structured-output.md` (Claude structured outputs, effort, pricing).

## Summary and recommendation

- **The scale is small enough to score every pair cheaply.** Run 1 has 146 nodes in the cross-filing labels and 2,139 same-label pairs in total (ran). Even ten times that is trivial for numpy cosine and RapidFuzz. "Never all pairs" should apply to LLM calls only. Cheap scores over every same-label pair let the evaluation sample from outside the blocked set, which is what keeps recall honest (sections 0, 1, 2).
- **Evaluation.** Report pairwise precision and recall of the *final clustering* (pairs in the same cluster), not of raw LLM answers, with Wilson 95% intervals. Draw the ~100 labelled pairs as a stratified sample over all same-label pairs: most from the blocked set, a band just below the blocking threshold, and a random draw from the rest, then weight by stratum size. Recall estimated only from blocked pairs measures the adjudicator, not the pipeline (Papadakis et al. call the missing part pair completeness). Tune thresholds and the prompt on NVDA pairs and score on AMD and INTC pairs, or split by a hash as the eval set does. Add B-cubed as a cluster-level number only if a full gold clustering is labelled for some labels (section 1).
- **100 pairs gives wide intervals.** 45 of 50 correct gives a Wilson interval of 0.79 to 0.96; 0 matches in 30 random low-similarity pairs only bounds that stratum's match rate below 0.11 (ran). Say so in the README next to the number (section 1.4).
- **Blocking.** Union of three cheap rules plus embedding neighbours, all within a label: equal after normalization and suffix stripping; RapidFuzz `token_set_ratio` >= 90; an acronym rule (short all-caps name equals the initials of a long name: GDPR, OBBBA, TSMC); a shared rare token; and the top k embedding neighbours or cosine above a threshold chosen on tuning labels. On run 1 the lexical rules alone flag 49 of 2,139 pairs and catch every duplicate listed in the task (Groq x3, Huawei, Samsung, Microsoft, GDPR, OBBBA, Mr. Teter) (ran). They also flag family/member pairs (Blackwell vs NVIDIA Blackwell Ultra platform, GeForce vs GeForce NOW, GDPR vs U.K. GDPR), which is the adjudicator's job to reject (section 2).
- **What to embed.** Voyage gives no guidance on short strings or entity names, and its only symmetric-task advice is for a legacy model. Embed one templated text per node, such as `"{label}: {name}"`, with the same `input_type` on both sides, and compare against name-plus-evidence-span on the tuning labels before picking. `ingest/voyage.py` today sends one text per request with `input_type` query or document; entity embeddings would need a third path (no new dependency) (section 2.3).
- **Clustering.** Union-find (connected components) over "same" decisions, as Splink and neo4j-graphrag do, then check each cluster for any pair the adjudicator called "distinct". Treat such a cluster as a contradiction: do not merge it, write it to the log, and send it to the user, rather than running correlation clustering. Correlation clustering (pivot, 3-approximation) assumes a +/- label on every pair, which blocking does not give (section 3).
- **Libraries.** Splink 5.0.0 (181 MB installed, DuckDB and PyArrow), dedupe 3.0.3 (27 packages, last release 2024-08) and recordlinkage 0.16 (last release 2023-07, pandas < 3) are built for probabilistic record linkage over millions of multi-field records and are overkill here. `rapidfuzz==3.14.6` is already in `uv.lock` through edgartools, so making it direct adds no package. `cleanco==2.3` is pure Python, MIT, no dependencies, but has pitfalls (next point). jellyfish adds nothing RapidFuzz lacks. Clustering needs no library (networkx 3.6.1 is already locked through ragas, or 20 lines of union-find) (section 4).
- **Legal suffixes.** cleanco 2.3 strips one trailing suffix per pass: "Huawei Technologies Co. Ltd." becomes "Huawei Technologies Co." unless called to a fixed point; it never strips "N.A."; it strips "Company" (from "Taiwan Semiconductor Manufacturing Company Limited", "The Coca-Cola Company") and turns "Company" alone into an empty string (ran). GLEIF's ISO 20275 list (4,002 forms) is authoritative for legal forms but lists East Asian forms only in native script and Delaware's "Corporation" with no "Inc." abbreviation, so it does not cover how 10-Ks print these names (ran). Recommendation: a short hand-written suffix list for the forms that occur in the corpus, stripped repeatedly from the end only, used for the match key while the stored name stays verbatim (section 5).
- **LLM adjudication.** Peeters, Steiner and Bizer (EDBT 2025) found no single best prompt; GPT-4 did best with a detailed task description and forced yes/no output, and related demonstrations helped on one dataset and hurt on two. Its false positives came mostly from over-weighting shared attributes like brand and ignoring product type or model differences, which is the family/member risk here. Wang et al. (COLING 2025) and Fu et al. (SIGMOD 2026) report that choosing among candidates or clustering a set beats isolated yes/no pairs. Recommendation: keep GRAPH-LAYER's `same` / `related` / `distinct`, make `related` carry a reason (`family_member`, `parent_subsidiary`, `variant`, `other`), require quoted spans from both nodes' evidence, and never merge on `related` (section 6).
- **Other tools.** GraphRAG merges only exact upper-cased title plus type. LightRAG merges exact sanitized names and offers a manual merge that deletes the source nodes. neo4j-graphrag's fuzzy resolver compares all pairs per label at WRatio >= 0.8 and merges with `apoc.refactor.mergeNodes(..., properties: 'discard')`; its set consolidation can leave one node in two sets (ran on a copy). LLM Graph Builder flags pairs by substring, edit distance < 3 or cosine > 0.97 for a human to confirm, then merges destructively. None keeps a merge log or can undo a merge. Recommendation: resolve before load and write canonical keys in Step 10, never merge nodes inside Neo4j (section 7).
- **XBRL.** `companyfacts` and `companyconcept` hold only entity-wide, non-dimensional facts from standard taxonomies, each with `accn`, `fy`, `fp`, `form`, `start`, `end`, `val`. NVIDIA's FY2026 `us-gaap:Revenues` fact carries accession 0001045810-26-000021 and value 215,938,000,000, the same figure run 1 extracted twice, as "Revenue" and "Total revenue" (checked). In run 1, 151 of 175 MetricValues are filer-level and could be matched by accession, period and value; the 24 segment and product metrics could not, since dimensional facts are excluded. 10 requests per second, a declared User-Agent, no key. Background only: the decision is still open (section 8).

## 0. What run 1 holds (ran)

From `data/extract/0001045810-26-000021-run1.jsonl` (389 nodes, 571 edges, 128 flags):

| Label | Nodes | Same-label pairs | Lexical candidates (section 2) |
|---|---|---|---|
| MetricValue | 175 | per filing | n/a |
| RiskFactor | 68 | per filing | n/a |
| Product | 42 | 861 | 18 |
| Organization | 39 | 741 | 8 |
| Regulation | 27 | 351 | 16 |
| Person | 16 | 120 | 1 |
| Agreement | 9 | 36 | 4 |
| LegalProceeding | 6 | 15 | 2 |
| Segment | 6 | 15 | 0 |
| Facility | 1 | 0 | 0 |
| Total (cross-filing labels) | 146 | 2,139 | 49 |

Duplicate kinds seen, each needing a different rule:

- Legal suffix and punctuation: Groq / Groq, Inc / Groq, Inc.; Microsoft / Microsoft Corporation.
- Short form vs full legal name: Huawei / Huawei Technologies Co. Ltd.; Samsung / Samsung Electronics Co., Ltd.
- Acronym vs expansion: GDPR / General Data Protection Regulation; OBBBA / One Big Beautiful Bill Act.
- Honorific plus surname vs full name: Mr. Teter / Timothy S. Teter.
- Brand prefix: CUDA / NVIDIA CUDA; Omniverse / NVIDIA Omniverse; DRIVE / NVIDIA DRIVE.
- Not duplicates but lexically close: GDPR / U.K. GDPR; GeForce / GeForce NOW; NVLink / NVLink Fusion; Blackwell / GB200 (family vs member, no lexical overlap at all).
- Paraphrased descriptions as names: "export restrictions and export licensing requirements targeting China's semiconductor and supercomputing industries" with and without "August 2022 USG"; three Groq licence agreements worded differently.
- Within one filing, MetricValue duplicates differ only in concept wording: "Revenue" and "Total revenue" for FY2026, both 215,938 USD millions. Their keys differ because the key includes the concept.
- Cross-label duplicates exist (Agreement "Mellanox acquisition" vs Organization "Mellanox"; Segment "Data Center" vs Product "NVIDIA Data Center platform"). Blocking within a label will not see these by design; they are relations, not merges.

## 1. Evaluation methodology

### 1.1 Pairwise versus cluster metrics

- Menestrina, Whang and Garcia-Molina (PVLDB 3(1), 2010) show that pairwise F1, cluster F1 and other ER measures can rank the same systems differently, and propose generalized merge distance (https://vldb.org/pvldb/volumes/3/paper/Evaluating%20Entity%20Resolution%20Results). The practical point: name the metric and say why.
- Pairwise metrics count every pair inside a cluster, so a cluster of size n carries n(n-1)/2 pairs and large clusters dominate. Amigó et al. (Information Retrieval 12(4), 2009, doi:10.1007/s10791-008-9066-8) show that only B-cubed satisfies all their formal constraints for extrinsic clustering metrics. B-cubed needs a gold clustering of every item scored, not a sample of pairs.
- Recommendation for Step 9: the RUNBOOK asks for pairs, so the headline is pairwise precision and recall, computed on the *final clusters* (a labelled pair counts as predicted-same if both nodes end in one cluster). Report the raw adjudicator accuracy on the pairs it saw as a second row, since transitive closure can create merges no one adjudicated. If the user labels full clusters for one small label (Organization in one filing is 39 nodes), B-cubed on that label is cheap to add. Splink's docs say a model "cannot be meaningfully summarised by just one of these performance measures" and that F-score "does not account for class imbalance" (https://moj-analytical-services.github.io/splink/topic_guides/evaluation/edge_metrics.html); report precision and recall, not F1 alone.

### 1.2 Why sampling only blocked pairs overstates recall

- Papadakis, Skoutas, Thanos and Palpanas, "A Survey of Blocking and Filtering Techniques for Entity Resolution" (arXiv:1905.06167 v4), define pair completeness PC = |D(B)| / |D(E)| (duplicates kept by blocking over all duplicates), pairs quality PQ = |D(B)| / ||B|| and reduction ratio RR = 1 - ||B|| / ||E|| (ran: text extracted from the PDF). End-to-end recall is PC times the adjudicator's recall on blocked pairs. Labels drawn only from blocked pairs measure the second factor and assume PC = 1.
- Matches are rare among all pairs. Marchant and Rubinstein (PVLDB 10(11), 2017, arXiv:1703.00617) cite "extreme class imbalance" as the reason random sampling needs very many labels, and propose OASIS, an adaptive importance sampler that cut labels by up to 83% in their experiments. Binette et al. (The American Statistician, 2023, arXiv:2210.01230) and Binette et al. (arXiv:2404.05622, 2024) argue for labelling whole entities (clusters) and give estimators that correct for sampling bias, with the `er-evaluation` package.
- At this scale OASIS is unnecessary: every same-label pair can be scored for free, so a fixed stratified design works.

### 1.3 A stratified design for about 100 pairs

A suggestion for the grilling, not a published recipe:

1. Population: every same-label pair in the cross-filing labels across the filings resolved, each with a cheap score (the maximum of the lexical rules and embedding cosine).
2. Strata: S1 = pairs the blocker sends to the adjudicator; S2 = a band just below the blocking threshold (the pairs most likely to be missed matches); S3 = everything else.
3. Allocation, for example 60 / 25 / 15, drawn with a fixed seed. S3 is huge and nearly all non-matches; 15 pairs there mainly detect a gross blocking failure.
4. Estimate the number of true matches as the sum over strata of (stratum size) x (match rate in its sample). Recall = matched-and-merged / estimated true matches, both weighted the same way. Precision comes from pairs the resolver merged, which are almost all in S1.
5. Also report, per stratum, the raw counts, so a reader can see that S3 recall rests on, say, 0 matches in 15.
6. Hand labels for S1 can double as the adjudicator's accuracy check.

Alternative if the user prefers simplicity: label every pair in S2 (it is small at this scale) and report recall only "within the candidate band", saying plainly that matches outside the band are not measured.

### 1.4 Small-sample intervals

Wilson 95% intervals (ran):

| Correct / n | Interval |
|---|---|
| 45 / 50 | 0.786 to 0.957 |
| 27 / 30 | 0.744 to 0.965 |
| 18 / 20 | 0.699 to 0.972 |
| 50 / 50 | 0.929 to 1.000 |
| 30 / 30 | 0.886 to 1.000 |
| 0 / 15 | 0.000 to 0.204 |
| 0 / 30 | 0.000 to 0.114 |

The project already reports Wilson intervals for extraction accuracy (BUILD-LOG, Step 8). For weighted stratified estimates, a stratified bootstrap with a fixed seed matches what the eval harness does for its cells.

### 1.5 Keeping tuning and scoring apart

Thresholds (blocking cutoff, k neighbours) and the adjudication prompt get tuned on labelled pairs. Scoring on the same pairs inflates both numbers. Options: tune on the NVDA filings and score on AMD and INTC pairs; or split labelled pairs by a hash of the pair key, as the eval set splits questions. Either way, the scored pairs are drawn and labelled before the final prompt is frozen, and are not looked at while tuning. This note first said the labels must be the user's, like the extraction review. The Step 9 grilling (2026-10-10) decided otherwise: a labeller agent on gpt-6-luna labels both sets, and every score carries the label "agent-labelled".

## 2. Blocking

### 2.1 What the literature says

- Paulsen, Govind and Doan, "Sparkly" (PVLDB 16, 2023, p. 1507, https://www.vldb.org/pvldb/vol16/p1507-paulsen.pdf), found that top-k TF-IDF blocking with Lucene beat 8 state-of-the-art blockers, including deep-learning ones, and recommended top-k blocking as a baseline. Top-k per record (keep the k nearest neighbours) avoids picking one global similarity cutoff that suits neither short nor long names.
- Papadakis et al. (above) frame blocker choice as the trade between PC and RR. The usual way to set a threshold is to fix a PC target on labelled data and take the smallest candidate set that reaches it. Here the adjudicator is cheap per pair, so the target can be PC near 1.0 on the tuning labels.

### 2.2 Lexical rules on run 1 (ran)

Script `t3.py` in the scratch directory, run with `cleanco==2.3` and `rapidfuzz==3.14.6`. Within each label, a pair is a candidate if any of:

- names equal after NFKC, casefold, whitespace collapse and suffix stripping (cleanco called to a fixed point);
- `fuzz.token_set_ratio` >= 90 on the stripped names;
- acronym: the shorter name is 2 to 8 upper-case characters and the longer name's word initials (ignoring of/the/and/for/on/in) start with it;
- a shared token that appears in at most 3 names of that label, is longer than 2 characters and is not a stop word (nvidia, inc, co, ltd, company, corporation, act, gpus, platform were treated as stop words for this run).

Result: 49 candidates of 2,139 pairs (RR about 0.98). All the duplicates named in the task are in it. So are the family/member and variant pairs (Blackwell vs four Blackwell products, GeForce vs GeForce NOW, NVLink vs NVLink Fusion, DRIVE vs DRIVE Hyperion, GDPR vs U.K. GDPR) and some junk from the rare-token rule (Micron Technology vs Marvell Technology, Regulation FD vs General Data Protection Regulation). Plain RapidFuzz scores show why one global fuzzy cutoff fails: `WRatio` is 90 for both Groq / Groq, Inc. (same) and GeForce / GeForce NOW (different), and 24 for GDPR / General Data Protection Regulation (same) (ran).

Lexical rules cannot see Blackwell vs GB200 or any pure synonym. That is the embedding arm's job, and also the adjudicator's `related` outcome.

### 2.3 Embeddings for entity names

- Voyage `input_type` is `None`, `query` or `document`. With `query` or `document`, Voyage prepends "Represent the query for retrieving supporting documents:" or "Represent the document for retrieval:". Embeddings with and without `input_type` are compatible. Voyage embeddings are normalized to length 1, so cosine equals dot product. voyage-4-large takes up to 1,000 texts and 120K tokens per request, at 256, 512, 1024 (default) or 2048 dimensions (https://docs.voyageai.com/docs/embeddings, https://docs.voyageai.com/docs/faq).
- Neither page gives guidance on short texts, entity names, or symmetric similarity for current models; the only clustering pointer is to instructions for the legacy `voyage-large-2-instruct` (unconfirmed for voyage-4-large). Using `document` on both sides, or `None` on both, keeps the comparison symmetric; which works better for names is not documented and needs a test.
- Run 1's own evidence: names alone are short and ambiguous ("DRIVE", "Grace", "Clara"). Adding the label and one evidence span gives context but makes two mentions of the same entity in different contexts look less alike. Recommendation: compute both variants on the tuning pairs and keep the one with higher PC at the same candidate count. Cost is negligible either way (a few thousand short texts against Voyage's free allowance of 200M tokens per account, per the FAQ).
- Implementation note: `ingest/voyage.py` posts one text per request with `input_type` query or document and truncation off. Entity embeddings would add a batched path and their own disk cache keyed by model, input type and text hash, like the query cache. No new dependency.

### 2.4 Thresholds in comparable tools

neo4j-graphrag's fuzzy and spaCy resolvers default to `similarity_threshold=0.8`; LLM Graph Builder uses cosine > 0.97 or substring or edit distance < 3 (section 7). Both are fixed defaults, not tuned on data. For FilingIntel, choose k and the cosine cutoff on the tuning labels and record them in the run report.

## 3. From pairwise decisions to clusters

- Transitive closure (connected components over "same" edges) is the standard step. Splink's `cluster_pairwise_predictions_at_threshold` calls `solve_connected_components` on pairs above a probability threshold (splink v5.0.0, `splink/internals/clustering.py`). neo4j-graphrag does the same over all pairs above its threshold (section 7).
- The risk is chaining: A=B and B=C merges A with C even if A and C are clearly different. With family/member pairs in the candidate set (Blackwell, GeForce), one wrong "same" can chain a product family into one node.
- Correlation clustering treats every pair as a + or - vote and minimizes disagreements. Bansal, Blum and Chawla introduced it (Machine Learning 56, 2004). Ailon, Charikar and Newman (JACM 55(5), 2008) gave the pivot algorithm, a 3-approximation that repeatedly picks a random pivot and clusters its positive neighbours (as described in later surveys, e.g. arXiv:2208.12636; the JACM text was not fetched). Its guarantees assume every pair has a label. Blocking leaves most pairs unjudged, and treating "unjudged" as "-" would block legitimate transitive merges.
- Recommendation: union-find over `same`, then for each resulting cluster check every pair inside it that has an explicit `distinct` or `related` decision. If any exists, the cluster is a contradiction: keep its members separate, log the contradiction with all decisions involved, and add it to the user's review list. Also log a cluster that grows beyond a size cap (say 5 names) for review, because in one filing no real entity in run 1 had more than three surface forms. Counts of contradictions go into the run report.
- An optional extra guard: adjudicate the pair (A, C) whenever A=B and B=C but A-C was never judged, before closing the cluster. At this scale the extra calls are few.

## 4. Libraries

PyPI metadata read 2026-10-10; installed sizes measured in a clean Python 3.11 environment, excluding pip and setuptools (ran).

| Package | Latest (date) | Licence | Runtime dependencies | Installed | Repo last push | Fit for ~1–3k nodes, same label, LLM-adjudicated |
|---|---|---|---|---|---|---|
| splink | 5.0.0 (2026-09-28) | MIT | duckdb, pyarrow, sqlglot | 181 MB, 3 deps | 2026-10-06 | Overkill. Fellegi-Sunter model over multi-field records, trained by EM; our records are one name field plus evidence. Its clustering and edge-metric docs are useful reading. |
| dedupe | 3.0.3 (2024-08-15) | MIT | scikit-learn, numpy, BTrees, zope.index, affinegap and 20 more | 132 MB, 27 packages | 2025-07-29 | Overkill. Active-learning matcher that wants its own labelled training loop; duplicates the LLM's role. |
| recordlinkage | 0.16 (2023-07-20) | BSD-3-Clause | pandas < 3, numpy, scipy, scikit-learn, jellyfish, joblib | 169 MB, 15 packages | 2024-02-21 | Overkill and stale; pins pandas below 3. |
| rapidfuzz | 3.14.6 (2026-08-30) | MIT | none | 4.3 MB | 2026-09-28 | Fits. Already in `uv.lock` at 3.14.6 as an edgartools dependency, so a direct dependency adds no package. Needs the usual "ask before adding". |
| cleanco | 2.3 (2024-05-15) | MIT | none | under 0.1 MB | 2026-06-23 | Usable as a suffix list, with the pitfalls in section 5. Small enough to vendor the term list instead. |
| jellyfish | 1.2.1 (2025-10-11) | MIT (classifier) | none | 0.7 MB | 2026-07-24 | Not needed: Jaro-Winkler and phonetic codes add little for company and product names; RapidFuzz has Jaro-Winkler. |
| python-Levenshtein | 0.27.5 | GPL-2.0-or-later | Levenshtein | n/a | n/a | Avoid: GPL, and RapidFuzz covers it. |
| nameparser | 2.3.0 (2026-09-12) | LGPL | none | n/a | n/a | Could split "Timothy S. Teter" into parts for the Person rule; a surname-plus-honorific regex is enough for 16 people. |

Already locked, usable without a new top-level package: numpy 2.4.6 (cosine), networkx 3.6.1 via ragas (connected components), rapidfuzz 3.14.6 via edgartools. `requires_python` for rapidfuzz 3.14.6 is >= 3.11, which matches the project.

## 5. Legal suffix normalization

### 5.1 cleanco 2.3 (ran)

Source read from the installed wheel (`cleanco/clean.py`, `cleanco/termdata.py`; repo https://github.com/psolin/cleanco). `basename()` normalizes case and accents, removes `.`, `,` and `-` for comparison, then walks the term list once, longest terms first and then alphabetically, deleting a term each time the name ends with it. Because it is one pass, a multi-part suffix whose parts are separate terms is only partly removed.

| Input | `basename()` | Called to a fixed point |
|---|---|---|
| Groq, Inc. | Groq | Groq |
| Huawei Technologies Co. Ltd. | Huawei Technologies Co. | Huawei Technologies |
| Hon Hai Precision Industry Co., Ltd. | Hon Hai Precision Industry Co. | Hon Hai Precision Industry |
| Samsung Electronics Co., Ltd. | Samsung Electronics Co. | Samsung Electronics |
| Taiwan Semiconductor Manufacturing Company Limited | Taiwan Semiconductor Manufacturing Company | Taiwan Semiconductor Manufacturing |
| Computershare Trust Company, N.A. | unchanged | unchanged |
| Bank of America, N.A. | unchanged | unchanged |
| Hewlett Packard Enterprise Company | Hewlett Packard Enterprise | same |
| The Coca-Cola Company | The Coca-Cola | same |
| Cooley LLP | Cooley | same |
| Company | "" (empty) | "" |

Pitfalls this shows:

- "Co." and "Company" are terms, so they go even when part of how the name is used ("Lockheed Missiles and Space Company" becomes "Lockheed Missiles and Space"). This is fine for a match key and wrong for a display name.
- "N.A." (national bank) is not a term, so bank trustees keep it.
- A name that is only a suffix becomes empty; guard against empty keys.
- The term list includes two-letter forms from other jurisdictions ("ab", "ae", "as", "ag", "og", "ep", "sa"; `termdata.py`), so any name whose last word is one of them loses it ("Arm SE" becomes "Arm", ran). Correct for a Norwegian "AS", wrong for an English name ending in such a word. Not observed in run 1.
- "LLP" marks law and audit firms (Cooley, PricewaterhouseCoopers); stripping it is safe for matching.

### 5.2 GLEIF ISO 20275 Entity Legal Forms (ran)

- GLEIF publishes the ELF code list, version 1.6 released February 2026, as CSV and Excel (https://www.gleif.org/en/about-lei/code-lists/iso-20275-entity-legal-forms-code-list). The page does not state a licence for the list (unconfirmed).
- The CSV has 4,002 rows (3,790 active, 212 inactive) and 1,256 distinct abbreviations, with columns for local name, transliterated name, local and transliterated abbreviations, country and jurisdiction.
- It is not a ready suffix list for 10-K text. Delaware's "Corporation" has no abbreviation listed, so "Inc." and "Corp." are absent for Delaware; Taiwan's and Japan's forms appear only in native script (股份有限公司, 株式会社), so the English "Co., Ltd." that 10-Ks print for TSMC, Hon Hai and Samsung is not there. US federal "National Bank" does list "N.A.".
- Use: cite it as the authority for what a legal form is, and use it to check a hand list; do not load it as the stripping list.

### 5.3 Recommendation

A hand-written list in code, limited to forms that occur in the corpus (inc, incorporated, corp, corporation, co, company, ltd, limited, llc, llp, plc, n.a., s.a., n.v., ag, gmbh, b.v., compared after removing periods), stripped from the end only, repeatedly, after removing trailing commas and periods, and never to an empty string. The stripped form goes only into the match key; the stored name stays as the filing prints it, and every surface form is kept as an alias. "Holdings", "Group" and "Technologies" are left alone: "Lumentum Holdings" and "Lumentum" may be different legal entities (parent vs operating subsidiary), which is an adjudication question, not a normalization one.

## 6. LLM-based matching

### 6.1 Published results

- Peeters, Steiner and Bizer, "Entity Matching using Large Language Models" (arXiv:2310.11244 v4, EDBT 2025). Zero-shot GPT-4 reached F1 of 89.61 (WDC Products), 95.78 (Abt-Buy), 89.67 (Walmart-Amazon), 76.38 (Amazon-Google), 89.82 (DBLP-Scholar) and 98.41 (DBLP-ACM), beating the best fine-tuned PLM on 3 of 6 datasets. Findings relevant here:
  - "No single best prompt". For GPT-4 a detailed domain prompt with forced yes/no output averaged 87.00 F1 against 80.31 for the same prompt with free output.
  - Related in-context demonstrations helped GPT-4 on Amazon-Google (about +9 F1) and hurt it on WDC Products and Abt-Buy. Hand-written matching rules cost GPT-4 1 to 3 F1. Weaker models gained from both.
  - Error analysis of GPT-4 explanations: false positives on Walmart-Amazon were mostly "overemphasis on matching attributes like brand or model" (23 of 26) and "ignoring significant product type or model differences" (21 of 26), with accessory or variant confusion in 8. This is the family/member and variant risk in run 1 (GeForce vs GeForce NOW, GDPR vs U.K. GDPR).
- Wang et al., "Match, Compare, or Select?" (arXiv:2405.16884, COLING 2025) compare deciding each pair alone, comparing pairs, and selecting the match from a candidate set, and report that selecting, which lets the model see records together, does better; their ComEM framework combines strategies and models to cut cost.
- Fu et al., "In-context Clustering-based Entity Resolution with Large Language Models" (arXiv:2506.02509, SIGMOD 2026) ask the model to cluster a small set of records directly, with a guard against unstable clusters; they report up to 5x fewer API calls than pairwise matching.
- Narayan et al., "Can Foundation Models Wrangle Your Data?" (arXiv:2205.09911) is the early result that prompting large models reaches state-of-the-art on data integration tasks; the abstract does not give ER details.

### 6.2 Prompt design for Step 9

From the above plus the project's existing constraints (structured output, no sampling parameters, response cache):

- One call per *block* rather than per pair is worth trying: give the model one anchor node and its candidates (all same label), each with its name, label, and one or two evidence spans resolved from the chunk text, and ask for a decision per candidate. This is the "select" setting and costs fewer calls. Pairwise stays the fallback and is easier to score.
- Output per candidate: `decision` in `same` / `related` / `distinct`; `relation` when related (`family_member`, `parent_subsidiary`, `variant_or_successor`, `other`); `evidence` as quoted spans from the supplied evidence, checked in code as substrings the way Step 8 checks spans; a short `reason`. Forced categories follow the Peeters result for strong models.
- Spell out the hierarchy rule in the prompt: a product family and its members are `related`, not `same` (Blackwell vs GB200); a parent and its subsidiary are `related` (a holding company vs its operating unit); a regional variant of a law is `related` (GDPR vs U.K. GDPR); a short name, acronym or name without legal suffix for the same legal entity or instrument is `same`.
- Few-shot examples: optional, and to be measured. Peeters et al. found demonstrations can hurt the strongest model. If used, take them from the tuning split only.
- Model and effort follow the Step 8 extractor (`claude-sonnet-5-5`); `claude-extraction-structured-output.md` covers the API.

## 7. How other GraphRAG tools resolve entities

| Tool (version read) | Candidate pairs | Decision | Merge | Log / undo |
|---|---|---|---|---|
| Microsoft GraphRAG v3.3.0 (2026-10-08) | none; names are upper-cased at extraction (`graph_extractor.py`, `clean_str(record_attributes[1].upper())`) | exact equality of (title, type) in `_merge_entities` (`extract_graph.py`) | pandas groupby collects descriptions and text unit IDs | none |
| LightRAG v1.5.7 (2026-09-02) | none; `normalize_entity_name` sanitizes text (quotes, full-width symbols, spaces) | exact name equality | descriptions concatenated and summarized; `amerge_entities` merges named entities on request | `amerge_entities` calls `delete_node` on the sources (`utils_graph.py`); no log found |
| neo4j-graphrag-python 1.22.0 (2026-10-01) | all pairs within each label (`itertools.combinations`), no blocking | `FuzzyMatchResolver`: RapidFuzz `WRatio` with default processor, / 100 >= 0.8; `SpaCySemanticMatchResolver`: `en_core_web_lg` vectors, cosine >= 0.8; `SinglePropertyExactMatchResolver`: exact property | `apoc.refactor.mergeNodes(nodes, {properties: 'discard', mergeRels: true})` | none; destructive |
| Neo4j LLM Graph Builder v0.8.7 (2026-07-23) | same label and (one name contains the other, both over 2 chars) or (edit distance < 3, over 5 chars) or cosine > 0.97 (`DUPLICATE_SCORE_VALUE`, `DUPLICATE_TEXT_DISTANCE`), at most 100 groups | the user picks in the UI | `apoc.refactor.mergeNodes(..., {properties:"discard", ...})` | none; destructive |

Two details from the source:

- neo4j-graphrag's `_consolidate_sets` merges each pair into the first existing set it overlaps and stops. For pairs {a,b}, {c,d}, {b,c} a copy of the function returns [{a,b,c}, {c,d}], with c in two sets (ran on a copy of the function, not the package). A proper union-find avoids this.
- LLM Graph Builder's substring rule would merge GeForce with GeForce NOW and NVLink with NVLink Fusion, which is why it leaves the final call to a person.

None of the four keeps evidence for a merge or can reverse one. GRAPH-LAYER.md's merge log is the part FilingIntel adds. Since Step 8 writes nothing to Neo4j and Step 10 owns the load, Step 9 can resolve entirely outside Neo4j: an append-only decision log (pair, decision, evidence, prompt version, model, who decided) from which a canonical-key map is recomputed; undoing a merge is appending a revocation and recomputing. Step 10 then writes nodes under canonical keys with an `aliases` list. Where that log and map live is a grilling question.

## 8. SEC XBRL APIs (background for the open MetricValue decision)

- `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` returns all XBRL facts for one company; `companyconcept/CIK##########/{taxonomy}/{tag}.json` one concept; `frames/{taxonomy}/{tag}/{unit}/{period}.json` one fact per company for a calendar period (CY####, CY####Q#, CY####Q#I). Only standard taxonomies (us-gaap, ifrs-full, dei, srt), and only facts that apply to the entire filing entity. No authentication or key. Real-time updates, with a nightly bulk `companyfacts.zip` (https://www.sec.gov/search-filings/edgar-application-programming-interfaces).
- Fair access: at most 10 requests per second per user, and SEC may block excessive traffic (https://www.sec.gov/about/developer-resources). Declare a User-Agent such as "Sample Company Name AdminContact@<sample company domain>.com" (https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data). The project's EDGAR client already does both.
- One live check (fetched with WebFetch, not the project client): `companyconcept/CIK0001045810/us-gaap/Revenues.json` has top-level keys `cik`, `taxonomy`, `tag`, `label`, `description`, `entityName`, `units`; unit `USD`; each fact has `start`, `end`, `val`, `accn`, `fy`, `fp`, `form`, `filed` and sometimes `frame`. The FY2026 fact is start 2025-01-27, end 2026-01-25, val 215938000000, accn 0001045810-26-000021, form 10-K, frame CY2025.
- What this means for MetricValue: run 1 extracted that same figure twice ("Revenue" and "Total revenue", FY2026, 215,938 USD millions). A deterministic link from a filer-level MetricValue to a tag could match on accession number, period end and scaled value, without trusting the model's concept wording, and would also collapse such within-filing duplicates. It covers 151 of run 1's 175 MetricValues (subject = the filer). The 20 segment and 4 product metrics are dimensional facts, which these APIs exclude; they would need the filing's own XBRL instance (whether edgartools exposes dimensional facts for these filings is unconfirmed). One value can also match several tags (for example a subtotal reported under two concepts), so a value match needs a tie rule. This is a new EDGAR data source and stays a proposal (OPEN-DECISIONS, Step 9).

## Open questions for the grilling

1. Population and split for the labelled pairs: which filings are resolved in Step 9 (NVDA only, or all 24), and is tuning on NVDA and scoring on AMD/INTC acceptable?
2. Stratum sizes for the ~100 pairs, or the simpler "label all of S2" option.
3. Headline metric: pairwise precision and recall on final clusters, with adjudicator accuracy as a second row. Add B-cubed for one fully labelled label?
4. What `related` produces: nothing (no merge), or a new edge (product family, parent/subsidiary) in a later ontology version.
5. Where the decision log and canonical-key map live (JSONL under `data/` like extraction, a Postgres table, or both), and whether Step 10 or Step 9 applies it.
6. Whether MetricValue and RiskFactor are in Step 9's scope at all, given they are per filing; within-filing MetricValue duplicates could wait for the XBRL decision.
7. Dependencies: `rapidfuzz` direct (already locked), `cleanco` or a hand list.
8. Embedding text: name only vs name plus label plus span, chosen on tuning labels.

## Sources

- Menestrina, Whang, Garcia-Molina, "Evaluating Entity Resolution Results", PVLDB 3(1), 2010. https://vldb.org/pvldb/volumes/3/paper/Evaluating%20Entity%20Resolution%20Results
- Amigó, Gonzalo, Artiles, Verdejo, "A comparison of extrinsic clustering evaluation metrics based on formal constraints", Information Retrieval 12(4), 2009. https://www.doi.org/10.1007/S10791-008-9066-8
- Papadakis, Skoutas, Thanos, Palpanas, "A Survey of Blocking and Filtering Techniques for Entity Resolution", arXiv:1905.06167 v4. https://arxiv.org/abs/1905.06167
- Marchant, Rubinstein, "In Search of an Entity Resolution OASIS", PVLDB 10(11), 2017. https://arxiv.org/abs/1703.00617
- Binette et al., "Estimating the Performance of Entity Resolution Algorithms: Lessons Learned Through PatentsView.org", The American Statistician, 2023. https://arxiv.org/abs/2210.01230
- Binette et al., "How to Evaluate Entity Resolution Systems: An Entity-Centric Framework", 2024. https://arxiv.org/abs/2404.05622
- Paulsen, Govind, Doan, "Sparkly: A Simple yet Surprisingly Strong TF/IDF Blocker for Entity Matching", PVLDB 16, 2023. https://www.vldb.org/pvldb/vol16/p1507-paulsen.pdf
- Ailon, Charikar, Newman, "Aggregating Inconsistent Information: Ranking and Clustering", JACM 2008 (summarized via https://arxiv.org/pdf/2208.12636; journal text not fetched).
- Peeters, Steiner, Bizer, "Entity Matching using Large Language Models", EDBT 2025. https://arxiv.org/abs/2310.11244 and https://arxiv.org/html/2310.11244v4
- Wang et al., "Match, Compare, or Select? An Investigation of Large Language Models for Entity Matching", COLING 2025. https://arxiv.org/abs/2405.16884
- Fu et al., "In-context Clustering-based Entity Resolution with Large Language Models: A Design Space Exploration", SIGMOD 2026. https://arxiv.org/abs/2506.02509
- Narayan et al., "Can Foundation Models Wrangle Your Data?". https://arxiv.org/abs/2205.09911
- Voyage AI, Text embeddings and FAQ. https://docs.voyageai.com/docs/embeddings, https://docs.voyageai.com/docs/faq
- Splink docs, edge metrics and cluster evaluation. https://moj-analytical-services.github.io/splink/topic_guides/evaluation/edge_metrics.html; source `splink/internals/clustering.py` at v5.0.0.
- PyPI JSON: https://pypi.org/pypi/{splink,dedupe,recordlinkage,rapidfuzz,cleanco,jellyfish,python-Levenshtein,nameparser}/json
- cleanco source (2.3 wheel; https://github.com/psolin/cleanco).
- GLEIF ISO 20275 ELF code list v1.6. https://www.gleif.org/en/about-lei/code-lists/iso-20275-entity-legal-forms-code-list
- GraphRAG v3.3.0: `packages/graphrag/graphrag/index/operations/extract_graph/{extract_graph,graph_extractor}.py`. https://github.com/microsoft/graphrag
- LightRAG v1.5.7: `lightrag/{operate,utils,utils_graph}.py`. https://github.com/HKUDS/LightRAG
- neo4j-graphrag-python 1.22.0: `src/neo4j_graphrag/components/resolver.py`. https://github.com/neo4j/neo4j-graphrag-python
- Neo4j LLM Graph Builder v0.8.7: `backend/src/graphDB_dataAccess.py`. https://github.com/neo4j-labs/llm-graph-builder
- SEC: EDGAR APIs https://www.sec.gov/search-filings/edgar-application-programming-interfaces; developer resources https://www.sec.gov/about/developer-resources; accessing EDGAR data https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data; https://data.sec.gov/api/xbrl/companyconcept/CIK0001045810/us-gaap/Revenues.json
