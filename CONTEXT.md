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
A span of one section with the ID `{accession_no}:{ordinal}`. Retrieval returns chunks and answers cite them.
_Avoid_: passage, document, node

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
