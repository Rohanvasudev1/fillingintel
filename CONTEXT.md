# FilingIntel

Citation-grounded question answering over the 10-K and 10-Q filings of NVIDIA, AMD and Intel, built to compare retrieval strategies on a fixed eval set.

## Corpus

**Filing**:
One 10-K or 10-Q document from EDGAR, identified by its accession number.
_Avoid_: document, report

**Section**:
A labelled span of a filing's parsed text, such as an Item or the preamble.
_Avoid_: part, or item when meaning the span

**Preamble**:
The section holding a filing's text before its first located Item. In some filings it carries real disclosure, not only the cover page.
_Avoid_: cover page, front matter

**Chunk**:
A span of one section with the ID `{accession_no}:{ordinal}`. Retrieval returns chunks, answers cite them, and graph evidence points at them.
_Avoid_: passage, document

## Eval

**Eval record**:
One question with its class, gold answer, gold chunks, hard negatives, provenance and split.
_Avoid_: test case, sample

**Gold chunk**:
A chunk that holds evidence needed to answer an eval record's question.
_Avoid_: reference context, ground truth

**Hard negative**:
A chunk that looks like evidence but is not. Its label says how it misleads: other period, same filing or peer company.
_Avoid_: distractor

**Split**:
Whether an eval record is `dev`, used for tuning and interim scores, or `test`, scored once for the final benchmark.

**Agent-drafted**:
The provenance of an eval record that an agent wrote and no human reviewed. Every result on such records carries this label.

**Judge**:
The model that scores answers for faithfulness, relevancy, citation support and decline correctness. Its numbers carry the label "uncalibrated" until calibration.
_Avoid_: grader, evaluator

**Spot check**:
A small comparison of one judge against a stronger reference judge, used to choose a judge model. It is not calibration.
_Avoid_: validation

**Calibration**:
Measuring a judge's agreement with the user's own scores of the same answers, with a statistic suited to each score type. Only the user's scores count as hand scores.
_Avoid_: validation, alignment

**Corruption test**:
A check that plants known errors (a changed number, a swapped period, a wrong citation, an unsupported sentence) in real answers and counts how often a judge catches each kind. The truth is known by construction, so no human scores are needed. It is not calibration.
_Avoid_: adversarial test, perturbation calibration

## Graph

**Ontology**:
The fixed list of node labels, edge types, their allowed endpoints and required properties. The graph holds nothing outside it, and any change to it creates a new ontology version.
_Avoid_: schema, when meaning the graph's types rather than the Postgres tables

**Company**:
One of the three filers (NVIDIA, AMD, Intel), identified by its CIK.
_Avoid_: organization, issuer

**Organization**:
Any other company or body a filing names, such as a supplier, customer, competitor, partner or acquired business. Its role comes from its edges, not its label.
_Avoid_: supplier, competitor, subsidiary, when used as a type

**Segment**:
A reportable business segment of a filer, as its segment note defines it (NVIDIA: Compute & Networking, Graphics).
_Avoid_: division, business unit, market platform

**Market platform**:
A market a filer reports revenue by that is not a reportable segment (NVIDIA: Data Center, Gaming, Professional Visualization, Automotive).
_Avoid_: segment, end market

**Metric value**:
One figure a filing states for a named subject, a metric, a period and a unit class. A figure the same filing states as a percentage and as an amount is two metric values. A restated figure in a later filing is a new metric value, not an overwrite.
_Avoid_: fact, data point

**Unit class**:
The kind of quantity a metric value measures: currency, percent, per share, shares, count or other. Two metric values in different unit classes are never the same figure; the same figure at two scales (millions, billions) is.
_Avoid_: unit, when meaning the class rather than the printed unit

**Risk factor body**:
The text of a risk factor from the end of its heading to the next risk factor's heading or the end of the section. Every chunk it touches is evidence for that risk factor, not only the heading's chunk. A heading starts a line; a risk factor quoted from a sentence inside a body has no body of its own and does not end one.
_Avoid_: description, paragraph

**Persistence link**:
The edge from a risk factor in one filing to the same risk in the company's previous 10-K, possibly reworded or expanded. A risk factor has at most one predecessor; a factor the filer later splits may have several successors.
_Avoid_: risk factor match, lineage

**Structural node**:
A node taken from EDGAR metadata rather than from text: a Company, Filing, Period or Chunk. Its evidence is the filing record.

**Extracted node**:
A node an extractor read out of a chunk's text. It needs at least one chunk as evidence.
_Avoid_: entity, when the structural/extracted distinction matters

**Edge**:
A typed connection between two nodes, allowed only between the labels the ontology lists for its type. An extracted edge carries its evidence chunks itself.
_Avoid_: relationship (Neo4j's word, kept for quoting its docs), link

**Candidate triple**:
What the extractor returns for one chunk before validation. It becomes an edge only after it passes the ontology and evidence checks.
_Avoid_: fact, edge, before validation

**Evidence**:
The chunk or chunks a node or edge was read from. A node or edge with no evidence is rejected.
_Avoid_: source, provenance, when meaning the chunk pointer itself

**Evidence span**:
The quote from a chunk that states a node or edge. It must appear verbatim in the chunk's text after whitespace, quote and dash normalization, or the candidate is rejected.
_Avoid_: snippet, excerpt, citation

**Confidence**:
The extractor's own rating of one piece of evidence: `stated` (the span says it outright), `implied` (it needs the chunk's surrounding text) or `uncertain`. It belongs to the evidence, not to the node or edge, and stays "uncalibrated" until review results show what each level is worth.
_Avoid_: score, probability, strength

**Filer reference**:
A fixed name the extractor uses for a structural node it cannot see: this filing, the filer, or one of the other two filers by ticker. Code maps it to the accession number or CIK.
_Avoid_: entity ID, placeholder

**Review round**:
One pass in which the user judges a fresh random sample of 30 validated edges from one extraction run against their chunks. Its accuracy is the only extraction accuracy reported.
_Avoid_: spot check, audit

## Entity resolution

**Entity resolution**:
Deciding which extracted nodes of one label name the same real-world thing, so the graph holds one node for it.
_Avoid_: deduplication, entity linking

**Same entity**:
Two nodes refer to one real-world thing at the same level: `Groq` and `Groq, Inc.`, or a parent and the subsidiary name a filing uses interchangeably with it. A product family and its member (`Blackwell`, `GB200`) are different entities.
_Avoid_: similar, related

**Canonical key**:
The node key a set of same-entity nodes resolves to. Extracted keys are never rewritten; they point at it.
_Avoid_: master record, golden record

**Alias map**:
The mapping from every extracted node key to its canonical key, rebuilt by replaying the merge log.
_Avoid_: lookup table, synonym list

**Merge log**:
The append-only record of every merge and split, each with its deciding evidence and method (deterministic, LLM or user). A merge is undone by appending a split. A split here never means a group cut.
_Avoid_: audit trail, history

**Candidate pair**:
Two same-label nodes that a cheap rule (normalized name, string similarity, acronym, shared rare word or embedding neighbour) sends to the adjudicator. Only candidate pairs cost an LLM call.
_Avoid_: block, match

**Adjudicator**:
The model that judges a candidate pair `same`, `related` (family member, parent and subsidiary, or variant), `distinct` or `unsure`, quoting evidence from both nodes' chunks. Only `same` with verified quotes merges.
_Avoid_: matcher, resolver, when meaning the model alone

**Held-back group**:
Keys joined by `same` answers (and deterministic merges) that are not merged as one, because the group holds a `related` or `distinct` pair or has more names than the limit. From Step 9b it is cut into pieces rather than dropped.
_Avoid_: rejected group, conflict cluster

**Group cut**:
Removing the fewest `same` answers from a held-back group so that no `related` or `distinct` pair ends up in one piece. Deterministic merges are never cut. When several smallest cuts tie, a key they place differently stays unmerged.
_Avoid_: split (a merge-log undo), partition

**Piece**:
A set of keys left joined after a group cut. A piece within the name limit merges as one `llm` merge; a larger piece stays held back.
_Avoid_: subgroup, fragment

**Labeller**:
The agent that labels pairs for tuning and scoring the resolver. It runs on a different model family from the adjudicator and never sees the resolver's output.
_Avoid_: annotator, judge

**Labelled pair**:
A pair of same-label nodes judged same or different by the labeller, an agent on a different model from the resolver. The 100 labelled pairs score the resolver and are never used to tune it; every score on them carries the label "agent-labelled".
_Avoid_: gold pair, ground truth

## Communities

**Community graph**:
The graph communities are found on: extracted nodes other than metric values, joined by extracted edges and co-occurrence edges. Companies, filings, periods and chunks are left out.
_Avoid_: entity graph, knowledge graph (the whole Neo4j graph)

**Co-occurrence edge**:
A link in the community graph between two extracted nodes evidenced by the same chunk. It exists only for finding communities and is never stored as an edge in the graph.
_Avoid_: implicit edge, relationship

**Community**:
A group of extracted nodes that Leiden puts together at one level. A community at the higher level contains whole communities from the lower level.
_Avoid_: cluster, topic

**Derived node**:
A graph node computed from other nodes rather than from EDGAR metadata or a chunk. Communities are the only derived nodes; they carry no evidence of their own, and their reports cite their members' evidence chunks.
_Avoid_: computed node, virtual node

**Community report**:
A written summary of one community, made by a model from its members and their evidence. It ranks communities for a question; it is never itself cited.
_Avoid_: community summary, cluster description

**Map point**:
A statement written from one community's report and evidence chunks during global search. It cites chunks and carries a relevance score. The reduce step builds the final answer from the map points.
_Avoid_: partial answer, intermediate answer

## Retrieval

**Arm**:
One retrieval strategy that runs end to end on every eval record, so arms can be compared. The arms are `vector`, `graph_local`, `graph_traversal` and `graph_global`.
_Avoid_: pipeline, retriever, method

**Control arm**:
The `vector` arm, which every other arm is compared against. It is never weakened.
_Avoid_: baseline, when meaning the arm rather than a result

**Anchor**:
An extracted node that `graph_local` links to a question, by its name's similarity to the question text, as the starting point for collecting evidence. The three filers are never anchors.
_Avoid_: seed, entry point, entity link

**Fallback**:
A `graph_traversal` question whose generated Cypher still fails the guard, errors, times out or returns no chunk IDs after its one repair, and is answered by `graph_local` instead. The reason is recorded as `invalid`, `error`, `timeout` or `empty`. Every fallback is logged and counted.
_Avoid_: retry, recovery

**Cypher validity rate**:
The share of questions whose first generated Cypher passes the ontology check and runs without error inside the timeout. It is reported next to the rate after one repair, the non-empty rate and the fallback rate.
_Avoid_: accuracy, success rate

**Question filter**:
The company, period and form-type constraints an arm derives from the question text alone, never from an eval record's labels.
_Avoid_: metadata filter, gold filter

**Router**:
The step that gives each question a predicted class (`lookup`, `local`, `multi_hop`, `global` or `decline`) and either declines it or hands it to the arm a routing map names. It is benchmarked as its own row, beside the arms. It is not an arm, because it retrieves nothing itself.
_Avoid_: classifier, when meaning the whole step; dispatcher

**Predicted class**:
The class the router gives a question, compared with the eval record's gold class. The router never predicts `unanswerable`, since it cannot tell up front that the filings lack the answer.
_Avoid_: route, intent

**Routing map**:
The table from predicted class to arm. The pre-registered map is the one fixed in RESEARCH-PLAN (H6) before any run. The dev-best map is chosen from dev results afterwards and is always labelled as tuned on dev.
_Avoid_: routing table, policy

**Oracle router**:
A router that is given each question's gold class and uses the pre-registered routing map. Comparing it with the router separates misclassification from the map's choice of arms.
_Avoid_: perfect router, upper bound

**Router accuracy**:
The share of questions whose predicted class equals the gold class. A `decline` question counts only when the router declined it.
_Avoid_: routing precision, arm accuracy

## Checks

**Quality gate**:
The CI check on every pull request that fails the build when the control arm's retrieval scores fall below the committed gate baseline. It scores retrieval only, offline; answer and judge scores are checked by hand.
_Avoid_: eval gate, regression test

**Gate baseline**:
The committed retrieval scores the quality gate compares against. It changes only by a deliberate update in a pull request, never to get a failing one through.
_Avoid_: baseline, when meaning the baseline run of the eval harness
