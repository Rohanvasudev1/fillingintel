# Vendored tiktoken encoding

`9b5ad71b2ce5302211f9c61530b329a4922fc6a4` is tiktoken's `cl100k_base` encoding file.

- Source: https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken
- SHA-256: `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7` (tiktoken's own expected hash)
- File name: tiktoken's cache key, the SHA-1 of the source URL (tiktoken 0.14.0, pinned in uv.lock)

tiktoken loads this file when `ingest.chunker` is imported. Pointing `TIKTOKEN_CACHE_DIR` here (in `tests/conftest.py` and the CI workflow) means tests and CI never download it. `tests/test_tokenizer_offline.py` checks the hash and tokenizes with the network blocked.
