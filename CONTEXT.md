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
Measuring a judge's agreement with hand scores, as Cohen's kappa. Required before the final benchmark.

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

## Retrieval

**Arm**:
One retrieval strategy that runs end to end on every eval record, so arms can be compared. The arms are `vector`, `graph_local`, `graph_traversal` and `graph_global`.
_Avoid_: pipeline, retriever, method

**Control arm**:
The `vector` arm, which every other arm is compared against. It is never weakened.
_Avoid_: baseline, when meaning the arm rather than a result

**Question filter**:
The company, period and form-type constraints an arm derives from the question text alone, never from an eval record's labels.
_Avoid_: metadata filter, gold filter

## Checks

**Quality gate**:
The CI check on every pull request that fails the build when the control arm's retrieval scores fall below the committed gate baseline. It scores retrieval only, offline; answer and judge scores are checked by hand.
_Avoid_: eval gate, regression test

**Gate baseline**:
The committed retrieval scores the quality gate compares against. It changes only by a deliberate update in a pull request, never to get a failing one through.
_Avoid_: baseline, when meaning the baseline run of the eval harness
