# Anthropic response fixtures

Three answer-model responses for dev questions, one per answer status:

- `answered_q0072.json`: a cited answer (q0072, lookup)
- `declined_q0005.json`: an investment-advice decline (q0005)
- `not_found_q0011.json`: the filings lack the evidence (q0011, unanswerable)

Each file holds the question, the 10 retrieved chunk IDs and the response body. The
`recorded` field says where the body came from.

**Status: real responses**, recorded on 2026-10-05 from `claude-sonnet-5-5` at effort `high`
with prompt v1. The answer gives 22% and 14% for the two largest direct customers. The
decline is the status line alone. The not-found reply's first sentence has no citation, so
enforcement drops it. None of the three responses has a thinking block. To record them again,
with `DATABASE_URL`, `VOYAGE_API_KEY` and `ANTHROPIC_API_KEY` in `.env`:

    uv run --env-file .env python scripts/capture_anthropic_responses.py

The script calls the API directly, bypassing the response cache, and writes only the
response bodies. They hold no credentials. The tests replay them through a fake SDK client
and a fake answer model.

## Extraction responses (Step 8)

Four extractor responses for chunks of the NVDA FY2026 10-K fixture (`0001045810-26-000021`):

- `extract_risk_factor.json`: chunk 0029, one risk factor
- `extract_segment_note.json`: chunk 0123, the segment note and its table
- `extract_suppliers.json`: chunk 0012, the Business paragraphs naming suppliers and competitors
- `extract_statement_table.json`: chunk 0089, the statement of comprehensive income

Each file holds the chunk ID, the prompt version and the response body.

**Status: real responses**, recorded on 2026-10-09 from `claude-sonnet-5-5` at effort `high`
with extract prompt v1. Output tokens were 350, 5,033, 6,222 and 2,268; the suppliers reply
used 78% of the 8,000-token cap. The three longer replies have a thinking block. To record them
again, with `ANTHROPIC_API_KEY` in `.env`:

    uv run --env-file .env python scripts/capture_extract_responses.py

The script needs no database: it rebuilds the chunks from the gzipped fixture. A prompt change
fails `test_recorded_with_the_current_prompt` until the responses are recorded again.

## Adjudicator responses (Step 9)

Ten adjudicator responses for pairs of nodes from NVDA run 1 (`0001045810-26-000021`), with
chunk text from the NVDA FY2026 10-K fixture:

- `resolve_groq_license_agreements.json` and `resolve_groq_ip_arrangement.json`: the Groq agreements
- `resolve_huawei.json`: "Huawei" and "Huawei Technologies Co. Ltd."
- `resolve_gdpr_long_name.json` and `resolve_gdpr_uk.json`: GDPR against its long name and against U.K. GDPR
- `resolve_obbba.json`: OBBBA and the One Big Beautiful Bill Act
- `resolve_teter.json`: "Mr. Teter" and "Timothy S. Teter"
- `resolve_blackwell_gb200.json`: Blackwell and GB200
- `resolve_geforce_now.json`: GeForce and GeForce NOW
- `resolve_revenue.json`: Revenue and Total revenue for FY2026

Each file holds both nodes as run 1 extracted them, the prompt version and the response body.

**Status: real responses**, recorded on 2026-10-10 from `claude-sonnet-5-5` at effort `high`
with resolve prompt v1 (`resolve/v1@6cf68829`). Seven answer `same`; GDPR / U.K. GDPR is
`related` (variant); Blackwell / GB200 and GeForce / GeForce NOW are `related` (family
member). Every quote passes the span check. Output tokens were 193 to 295, none has a
thinking block, and the ten calls cost $0.10 (the estimate was $0.26). To record them
again, with `ANTHROPIC_API_KEY` in `.env` and the run 1 candidates file:

    env -u ANTHROPIC_BASE_URL uv run --env-file .env python scripts/capture_resolve_responses.py data/extract/0001045810-26-000021-run1.jsonl

A prompt change fails `test_the_replies_were_recorded_with_the_current_prompt` until the
responses are recorded again.
