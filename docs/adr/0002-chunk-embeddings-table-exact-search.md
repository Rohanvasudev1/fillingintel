# Embeddings in their own table, searched exactly, with voyage-4-large

We embed chunks with `voyage-4-large`, which has 1024 dimensions and a 32k-token context. It is the strongest general model Voyage offers, so no one can call the control arm a weak or odd baseline.

The vectors go in a separate `chunk_embeddings` table keyed on `(chunk_id, model)`, with a hash of the embedded text. A column on `chunks` was the alternative. The separate table leaves the approved `chunks` schema untouched, and a second model needs no migration.

Search is exact, with no HNSW or IVFFlat index. At about 2,000 chunks an exact search takes milliseconds, and an approximate index would add its own recall misses to the control arm's scores.

API truncation is off, and an oversized chunk fails the load, so no chunk is ever embedded in part. The preamble is embedded too, because two dev gold chunks are in Intel's 10-K preamble.

Decided on 2026-10-04 in the Step 5 grilling.

## Consequences
- Adding an ANN index changes the control. Do it only with a rerun of the baseline.
- Query embedding calls the Voyage API, so an eval run in CI needs a key or cached query vectors.
