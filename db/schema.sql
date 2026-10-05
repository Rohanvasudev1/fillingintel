-- FilingIntel Postgres schema (Step 3c). Safe to apply repeatedly; apply_schema() then
-- checks the live columns match the code, because IF NOT EXISTS skips changed tables.
-- resolve(chunk_id) returns substr(filings.parsed_text, char_start + 1, char_end - char_start).
-- Offsets are Python string indexes (code points); Postgres text positions in a
-- UTF-8 database are also code points, so the two agree.

CREATE TABLE IF NOT EXISTS filings (
    accession_no                 text PRIMARY KEY CHECK (accession_no ~ '^\d{10}-\d{2}-\d{6}$'),
    cik                          text NOT NULL,
    company_name                 text NOT NULL,
    form_type                    text NOT NULL CHECK (form_type IN ('10-K', '10-Q')),
    fiscal_period                text NOT NULL,
    report_date                  date NOT NULL,
    filing_date                  date NOT NULL,
    primary_document             text NOT NULL,
    parsed_text                  text NOT NULL,
    text_sha256                  text NOT NULL
                                 CHECK (text_sha256 = encode(sha256(convert_to(parsed_text, 'UTF8')), 'hex')),
    financial_statements_section text NOT NULL,
    parser_commit                text NOT NULL,
    loaded_at                    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        text PRIMARY KEY,
    accession_no    text NOT NULL REFERENCES filings (accession_no) ON DELETE CASCADE,
    cik             text NOT NULL,
    form_type       text NOT NULL CHECK (form_type IN ('10-K', '10-Q')),
    fiscal_period   text NOT NULL,
    section         text NOT NULL,
    char_start      integer NOT NULL CHECK (char_start >= 0),
    char_end        integer NOT NULL,
    ordinal         integer NOT NULL CHECK (ordinal >= 0),
    token_count     integer NOT NULL CHECK (token_count >= 0),
    tokenizer       text NOT NULL,
    chunker_version text NOT NULL,
    contains_table  boolean NOT NULL,
    CHECK (char_end > char_start),
    -- Same as Python's f"{accession_no}:{ordinal:04d}"; greatest() stops lpad truncating.
    CHECK (chunk_id = accession_no || ':'
           || lpad(ordinal::text, greatest(4, length(ordinal::text)), '0')),
    UNIQUE (accession_no, ordinal)
);

CREATE INDEX IF NOT EXISTS chunks_filter_idx ON chunks (cik, fiscal_period, form_type);
-- In the approved schema. UNIQUE (accession_no, ordinal) also serves lookups by accession.
CREATE INDEX IF NOT EXISTS chunks_accession_idx ON chunks (accession_no);

-- Step 5 (ADR-0002). One row per chunk and embedding model. The vector column has no
-- fixed dimension so a second model fits without a migration; the CHECK ties it to
-- `dimensions`. No ANN index: search is exact, a sequential scan over a few thousand
-- rows, so no index on model either. Needs the pgvector extension in a schema
-- on the search path (docker image and CI install it in public).
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id        text NOT NULL REFERENCES chunks (chunk_id) ON DELETE CASCADE,
    model           text NOT NULL,
    dimensions      integer NOT NULL CHECK (dimensions > 0),
    text_sha256     text NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
    api_token_count integer NOT NULL CHECK (api_token_count > 0),
    embedding       vector NOT NULL CHECK (vector_dims(embedding) = dimensions),
    embedded_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, model)
);
