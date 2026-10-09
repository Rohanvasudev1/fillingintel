# Claude models for per-chunk graph extraction

Research note for Step 8, written 2026-10-09. Step 8 sends one chunk per call and asks for candidate triples from the fixed ontology in `graph/ontology.py`, each with a verbatim `evidence_span` and a `confidence`. Sources: Anthropic's docs, fetched as Markdown on 2026-10-09 (append `.md` to any `platform.claude.com/docs/...` URL); the installed `anthropic` 1.11.0 wheel in `.venv`; the GraphRAG v3.3.0 source (tagged 2026-10-08); the LangChain, neo4j-graphrag and LangExtract sources named below; arXiv papers. Anything not confirmed from a primary source is marked "unverified". This note does not repeat `ragas-deepeval-claude-judge.md` (the sampling-parameter ban and `messages.parse` basics) or `openai-luna-judge.md`.

## Summary and recommendation

- All four models support structured outputs (`output_config.format`) and strict tool use. The schema rules are the same for every model: no `minimum`/`maximum`, no `minLength`/`maxLength`, `minItems` only 0 or 1, no recursion, and a cap of 24 optional parameters and 16 union-typed parameters per request (section 1.2).
- Claude Haiku 4.5 is a legacy model. The current Haiku is `claude-haiku-5-5`, at $0.10 / $0.50 per MTok, a tenth of Haiku 4.5's price. Haiku 4.5 also has no effort parameter and a 4,096-token cache minimum, so the Step 8 system prompt (about 2,500 tokens) would never cache on it (sections 1.1, 2).
- Recommendation: `claude-sonnet-5-5` at effort `medium` as the default extractor, with `claude-haiku-5-5` at effort `high` run on the same hand-checked sample as a cheap challenger. Opus 5.5 and Fable 5.1 cost 2x and 5x Sonnet per token and are not justified for one-chunk calls until the sample shows Sonnet missing triples.
- Use JSON outputs (`output_config.format` through `client.messages.parse`), not a tool. Sonnet 5.5, Opus 5.5 and Fable 5.1 reject forced tool use, and the docs name structured outputs as the replacement.
- Cost for one 10-K (about 155 chunks) at Sonnet 5.5: about $1.77 uncached, $1.04 with prompt caching, $0.52 with caching and the Batches API, if each reply is about 500 output tokens. Thinking tokens are billed as output, so with 2,500 output tokens per call the figures are $4.87, $4.14 and $2.07 (section 3).
- Confidence: ask for a three-level enum (`high`, `medium`, `low`), not a 0 to 1 number. The API cannot constrain a number to [0, 1], verbalized numbers cluster at 80 to 100% in multiples of 5, and neither form is calibrated until it is checked against hand labels (sections 1.2, 4.2).
- Evidence spans: check every span in code as an exact substring of the chunk text and drop triples that fail. Anthropic's Citations feature would give API-guaranteed character offsets, but it returns a 400 when combined with structured outputs (section 4.1).
- Entity keys: build them in code from the label and a normalized name. GraphRAG upper-cases names and merges on exact match; LangChain title-cases them; neo4j-graphrag merges exact names per label. None of them lets the model choose the key (section 4.4).
- Found in passing: `retrieve/pricing.py` lists Sonnet 5.5 cache reads at $0.20 per MTok. The pricing page now says $0.10 (0.05x base input). Answers do not use caching today, so no reported cost changes, but the table needs a new date before Step 8 uses cache reads (open question 7).

## 1. Models

### 1.1 Identity, limits, thinking and effort

Read 2026-10-09 from the models overview (https://platform.claude.com/docs/en/about-claude/models/overview) and each model page.

| | `claude-haiku-4-5-20251001` | `claude-haiku-5-5` | `claude-sonnet-5-5` | `claude-opus-5-5` | `claude-fable-5-1` |
|---|---|---|---|---|---|
| Status | Legacy | Current | Current | Current | Current |
| Context | 200K | 1M | 1M | 1M | 1M |
| Max output (sync) | 64K | 128K | 128K | 128K | 128K |
| Thinking | Manual extended (`budget_tokens`) | Adaptive, can be disabled at effort `high` or below | Adaptive; lowest setting `between_tools` | Adaptive, always on | Adaptive, always on |
| Effort | Not supported | `low` to `max`, default `medium` | `low` to `max`, default `high` | `low` to `max`, default `medium` | `low` to `max`, default `high` |
| Non-default `temperature`/`top_p`/`top_k` | Allowed without thinking | 400 error | 400 error | 400 error | 400 error |
| Forced `tool_choice` (`any`, `tool`) | Allowed without thinking | Works with adaptive thinking | 400 error | 400 error | 400 error |
| Knowledge cutoff | Feb 2025 | Jun 2026 | Jun 2026 | Jun 2026 | Jun 2026 |

Sources:
- Haiku 4.5: "Claude Haiku 4.5 is a legacy model; Claude Haiku 5.5 is the current Haiku model". Context 200K, max output 64K, thinking "Extended", default effort "Not supported", cutoff Feb 2025. https://platform.claude.com/docs/en/models/haiku-4-5/overview
- Haiku 5.5: "built for high-volume, latency-sensitive work such as classification, routing, extraction". Default effort `medium`. "Omit `temperature`, `top_p`, and `top_k`, since a non-default value for any of them returns a 400 error." https://platform.claude.com/docs/en/models/haiku-5-5/overview
- Sonnet 5.5: default effort `high`; "The lowest thinking setting is `between_tools`, which turns off up-front thinking. It works at `high` effort or below." `thinking: {"type": "disabled"}` returns a 400. https://platform.claude.com/docs/en/models/sonnet-5-5/overview and https://platform.claude.com/docs/en/models/sonnet-5-5/whats-new-sonnet-5-5
- Opus 5.5: "Adaptive thinking is always on and can't be turned off." https://platform.claude.com/docs/en/models/opus-5-5/overview
- Fable 5.1: thinking "Adaptive (always on)", default effort `high`. Anthropic positions it "for demanding reasoning and long-horizon agentic work" and says to start most workloads on Opus 5.5. https://platform.claude.com/docs/en/models/fable-5-1/overview
- Effort levels and which models accept `xhigh` and `max`: https://platform.claude.com/docs/en/build-with-claude/effort (table "Effort levels"). `low` is described as for "Simpler tasks that need the best speed and lowest costs". For Haiku 5.5: "Use `high` for knowledge work, longer agent tasks, and strict instruction following." For Sonnet 5.5: "Start with `high` unless your workload is agentic or latency-sensitive", and its levels are recalibrated against Sonnet 5, so run a fresh effort sweep.
- Sampling: on the 5.x models, Fable 5.1 and others, "non-default `temperature`, `top_p`, or `top_k` values return a 400 error on every request, regardless of whether thinking is used. On older models, the restriction applies only while thinking is on." https://platform.claude.com/docs/en/build-with-claude/thinking ("Sampling parameters").
- Forced tool use: "Claude Opus 5.5, Claude Sonnet 5.5, Claude Fable 5.1, and Claude Mythos 5.1 ... reject forced tool use on every request with a 400 error. On those models, use `tool_choice: {"type": "auto"}` with strict tool use or structured outputs instead." Same page, "Response prefill and forced tool use".
- Thinking tokens count toward `max_tokens` and are billed as output: "Thinking counts toward `max_tokens` even when the thinking content isn't returned." Effort page, Sonnet 5.5 section.
- On the Batches API, Haiku 5.5, Sonnet 5.5 and Opus 5.5 accept up to 300K output tokens with the `output-300k-2026-03-24` beta header (models overview, "Max output" note). Not needed here.
- Tokenizer: Claude 4.7 and later models use a tokenizer that "produces approximately 30% more tokens for the same text" than earlier Claude models. https://platform.claude.com/docs/en/about-claude/pricing. All token counts in this note are tiktoken `cl100k_base`; real Claude counts are unverified.

### 1.2 Structured outputs

From https://platform.claude.com/docs/en/build-with-claude/structured-outputs:

- GA on the Claude API for all five models above (list includes `claude-haiku-4-5-20251001`, `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`, `claude-fable-5-1`). No beta header.
- Supported: basic types, `enum` (strings, numbers, bools, nulls only), `const`, `anyOf` and `allOf` (no `allOf` with `$ref`), internal `$ref`/`$defs`, `default`, `required`, `additionalProperties: false` (required on objects), string formats such as `date` and `uuid`, `minItems` of 0 or 1.
- Not supported: recursive schemas; `minimum`, `maximum`, `multipleOf`; `minLength`, `maxLength`; array constraints beyond `minItems` 0 or 1. "If you use an unsupported feature, you'll receive a 400 error with details."
- Regex `pattern`: anchors, `*`, `+`, `?`, simple `{n,m}`, `[]`, `.`, `\d`, `\w`, `\s` and groups. No backreferences, lookaround or `\b`.
- Limits per request: 20 strict tools, 24 optional parameters across all strict schemas, 16 parameters with union types (`anyOf` or `"type": ["string", "null"]`). Larger grammars can fail with "Schema is too complex for compilation", with a 180-second compile timeout.
- Compiled grammars are cached for 24 hours from last use; changing the schema structure invalidates that cache, changing only `name` or `description` does not. The first request with a new schema has extra latency.
- Property order: "required properties appear first, followed by optional properties."
- Enum casing is not guaranteed: "Claude may return a value that differs from your schema only in capitalization ... Compare enum values case-insensitively."
- A schema property that asks for the model's reasoning "may lead to a `reasoning_extraction` refusal. Ask for a short explanation instead."
- Invalid outputs: `stop_reason: "refusal"` (HTTP 200, billed, may not match the schema) and `stop_reason: "max_tokens"` (may be truncated).
- Works with the Batches API, token counting and streaming. Incompatible with Citations (400 error) and with prefilling.
- "Grammars apply only to Claude's direct output, not to ... thinking." Thinking and structured outputs work together.
- Structured outputs add a system prompt, so input tokens rise slightly, and "Changing `output_config.format` invalidates prompt cache".

From the installed SDK (`anthropic` 1.11.0; PyPI's latest is 1.12.1, uploaded 2026-10-08, https://pypi.org/pypi/anthropic/json):
- `messages.parse(output_format=Model, output_config={"effort": ...})` merges the two: `merged_output_config = {**output_config, "format": transformed_output_format}` (`anthropic/resources/messages/messages.py` lines 1135 to 1143). This settles the "unverified" point in `ragas-deepeval-claude-judge.md`.
- `transform_schema()` keeps `enum`, `anyOf`, `description` and the supported string formats, and moves every other keyword into the description text (`anthropic/lib/_parse/_transform.py`, final block). That includes `pattern` and `const`, which the API itself supports. A schema that needs `pattern` has to be sent raw through `output_config={"format": {"type": "json_schema", "schema": ...}}`.
- The Batches API takes plain `MessageCreateParamsNonStreaming`, so `output_config` works there (`anthropic/types/messages/batch_create_params.py`), but there is no `parse` helper for batch results. The caller validates each result with Pydantic.

## 2. Prices and caching

Read 2026-10-09 from https://platform.claude.com/docs/en/about-claude/pricing. USD per MTok.

| Model | Input | 5m cache write | 1h cache write | Cache read | Output | Batch input | Batch output | Cache minimum |
|---|---|---|---|---|---|---|---|---|
| Haiku 4.5 | 1.00 | 1.25 | 2.00 | 0.10 | 5.00 | 0.50 | 2.50 | 4,096 |
| Haiku 5.5 (prompts up to 100K) | 0.10 | 0.125 | 0.20 | 0.01 | 0.50 | 0.05 | 0.25 | 512 |
| Sonnet 5.5 | 2.00 | 2.50 | 4.00 | 0.10 | 10.00 | 1.00 | 5.00 | 512 |
| Opus 5.5 | 4.00 | 5.00 | 8.00 | 0.20 | 20.00 | 2.00 | 10.00 | 512 |
| Fable 5.1 | 10.00 | 12.50 | 20.00 | 0.25 | 50.00 | 5.00 | 25.00 | 512 |

- Cache reads are 0.1x base input, except 0.05x on Opus 5.5 and Sonnet 5.5 and 0.025x on Fable 5.1 (pricing page, footnotes 1 and 2). Writes are 1.25x (5 minutes) or 2x (1 hour).
- Haiku 5.5 over 100K prompt tokens costs $0.50 / $2.50; irrelevant for one chunk.
- Batch: "50% discount on both input and output tokens". Caching multipliers "stack with other pricing modifiers, including the Batch API discount". Same page.
- Cache minimums: https://platform.claude.com/docs/en/build-with-claude/prompt-caching ("Cache limitations"). A shorter prompt "will be processed without caching, and no error is returned".
- Caching inside batches: supported, but "cache hits are provided on a best-effort basis. Users typically experience cache hit rates ranging from 30% to 98%". Put identical `cache_control` blocks in every request, and consider the 1-hour TTL because batches can take longer than 5 minutes. https://platform.claude.com/docs/en/build-with-claude/batch-processing ("Using prompt caching with Message Batches").
- Batch limits: 100,000 requests or 256 MB per batch; "most batches completing within 1 hour"; results expire if not done in 24 hours. Same page.
- What breaks the cache: changing `output_config.effort` or the thinking setting invalidates message blocks, and changing `output_config.format` invalidates the cache. Keep schema, effort and thinking fixed for a run. Prompt caching page, table "What invalidates the cache".
- Place `cache_control` on the last block that is identical across requests (the end of the system prompt), not on the chunk. Automatic caching puts the breakpoint on the last block, which here changes every call, so it would write and never read. Prompt caching page, "Explicit cache breakpoints".

## 3. Cost per 10-K

### 3.1 Inputs

- 10-K chunk counts, from `spikes/corpus_report.txt` ("Chunks" table): NVDA 132 and 137, INTC 150 and 145, AMD 193 and 175. Mean 155, maximum 193, total 932 for the six 10-Ks. The 24-filing corpus has 2,144 chunks.
- Median chunk: 723 `cl100k_base` tokens (p10 354, p90 796). Source: `docs/BUILD-LOG.md`, Step 3b evidence table.
- Ontology text: `graph.schema_text.schema_text()` returns 7,162 characters, 1,817 `cl100k_base` tokens (measured 2026-10-09 with the vendored encoding). With instructions and one or two examples, the system prompt is assumed to be about 2,500 tokens. That is above the 512-token minimum on every current model and below Haiku 4.5's 4,096.
- Output per call: 500 tokens (triples only, little or no thinking) or 2,500 tokens (triples plus adaptive thinking). Real thinking volume is unknown until a live sample.

### 3.2 Formula

Per call, with S system tokens, C chunk tokens and O output tokens (thinking included), prices p from section 2:

- Uncached: `(S + C) × p_in + O × p_out`
- Cached, sync: first call `S × p_write5m + C × p_in + O × p_out`; every later call `S × p_read + C × p_in + O × p_out`
- Batch: either line × 0.5. Batch with caching assumes every call after the first is a hit, which is the best case (30 to 98% observed).

Per 10-K, N = 155 chunks, S = 2,500, C = 723, in USD (computed 2026-10-09; Claude token counts may be about 30% higher):

| Model | O | Uncached | Cached | Batch | Batch + cache |
|---|---|---|---|---|---|
| Haiku 5.5 | 500 | 0.09 | 0.05 | 0.04 | 0.03 |
| Haiku 4.5 | 500 | 0.89 | 0.89 (no cache) | 0.44 | 0.44 |
| Sonnet 5.5 | 500 | 1.77 | 1.04 | 0.89 | 0.52 |
| Opus 5.5 | 500 | 3.55 | 2.09 | 1.77 | 1.04 |
| Fable 5.1 | 500 | 8.87 | 5.12 | 4.44 | 2.56 |
| Haiku 5.5 | 2,500 | 0.24 | 0.21 | 0.12 | 0.10 |
| Haiku 4.5 | 2,500 | 2.44 | 2.44 (no cache) | 1.22 | 1.22 |
| Sonnet 5.5 | 2,500 | 4.87 | 4.14 | 2.44 | 2.07 |
| Opus 5.5 | 2,500 | 9.75 | 8.29 | 4.87 | 4.14 |
| Fable 5.1 | 2,500 | 24.37 | 20.62 | 12.19 | 10.31 |

- The six 10-Ks are 6.0x these figures (932 chunks). Sonnet 5.5, cached, sync: about $6 to $25. The whole 24-filing corpus is about 13.8x one 10-K.
- Output dominates once thinking is on. Caching saves the system prompt but not the thinking, so effort and thinking settings move cost more than caching does.
- GraphRAG-style gleaning (section 4.3) adds at least one more call per chunk with the whole first reply in the context, so roughly doubles these figures.

## 4. Technique evidence

### 4.1 Evidence spans and verbatim checks

- Anthropic's hallucination guide recommends asking for "word-for-word quotes first" on long documents, and verifying claims by finding "a direct quote from the documents that supports it", retracting a claim when there is none. https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations
- Anthropic's Citations feature returns 0-indexed character ranges for plain-text documents, and "citations are guaranteed to contain valid pointers to the provided documents"; `cited_text` does not count toward output tokens. But "Citations cannot be used together with structured outputs" and returns a 400. https://platform.claude.com/docs/en/build-with-claude/citations
- Google's LangExtract (v1.7.1) asks for "exact text for extractions. Do not paraphrase", then aligns each extraction back to the source. Extractions it cannot locate get `char_interval = None`, and the README says to filter them out. It records alignment as `MATCH_EXACT`, `MATCH_GREATER`, `MATCH_LESSER` or `MATCH_FUZZY`, with fuzzy matching on by default. https://github.com/google/langextract/blob/v1.7.1/README.md and `langextract/core/data.py` (`AlignmentStatus`).
- So the pattern is common: models paraphrase or copy from examples often enough that tools check spans in code. The project's ADR-0004 already requires spans; the question for Step 8 is exact match versus a normalized match (whitespace, curly quotes). LangExtract's default fuzzy matching would weaken invariant 4's "real chunk" guarantee, so exact or whitespace-normalized matching with recorded offsets fits this project better. That is a judgement, not a sourced claim.

### 4.2 Verbalized confidence

- Tian et al. 2023, "Just Ask for Calibration" (EMNLP 2023): for RLHF models including Claude, "verbalized confidences emitted as output tokens are typically better-calibrated than the model's conditional probabilities", "often reducing the expected calibration error by a relative 50%" on TriviaQA, SciQ and TruthfulQA. It compared numeric probabilities with linguistic levels such as "almost certain" mapped to numbers (by a human survey, or fitted on held-out questions), and found models "can express their uncertainty with numerical probabilities as well or better than with words". It also reports "Claude-1 is much weaker at verbalizing its confidence". https://arxiv.org/abs/2305.14975 and https://arxiv.org/html/2305.14975
- Xiong et al. 2024, "Can LLMs Express Their Uncertainty?" (ICLR 2024): "LLMs, when verbalizing their confidence, tend to be overconfident". Verbalized values fall mostly in 80 to 100% and in multiples of 5, which the authors read as imitating how humans state confidence (Section 5.1, Figure 2). Sampling consistency predicts failures much better than one verbalized number (Section 5.3, Table 3), and all methods struggle on specialized knowledge. https://arxiv.org/abs/2306.13063 and https://arxiv.org/html/2306.13063
- Both papers score question answering, not extraction. No primary source found on calibration of per-triple confidence in information extraction (unverified gap).
- What follows for Step 8: a 0 to 1 number from one call carries little more than three levels, because it clusters near the top and moves in steps of 0.05. The API cannot enforce [0, 1] on a number (section 1.2), so a numeric field needs a code check anyway. An enum is enforced by the grammar, apart from casing. Either form needs a hand-checked sample before it means a probability; until then it is a ranking signal, labelled uncalibrated as the judge scores are.

### 4.3 GraphRAG's extraction prompt

From `packages/graphrag/graphrag/prompts/index/extract_graph.py` and `index/operations/extract_graph/` at tag v3.3.0 (https://github.com/microsoft/graphrag/tree/v3.3.0):

- Output is delimited text, not JSON: `("entity"<|><entity_name><|><entity_type><|><entity_description>)` and `("relationship"<|><source_entity><|><target_entity><|><relationship_description><|><relationship_strength>)`, separated by `##`, ending with `<|COMPLETE|>`.
- Entity names: the prompt asks for "Name of the entity, capitalized"; the parser then upper-cases names and types (`clean_str(record_attributes[1].upper())`, `graph_extractor.py` lines 146 to 158). `clean_str` only unescapes HTML and strips control characters (`graphrag/index/utils/string.py`).
- Merging: entities are grouped by exact `(title, type)` and relationships by exact `(source, target)`; descriptions and source text-unit IDs are collected into lists, and relationship weights are summed (`extract_graph.py`, `_merge_entities` and `_merge_relationships`). The paper says the same: "our analysis uses exact string matching for entity matching", and duplicates are tolerated because they cluster together later (Edge et al. 2024, Section 3.1.3, https://arxiv.org/abs/2404.16130).
- Strength: "relationship_strength: a numeric score indicating strength of the relationship", parsed with `float(...)` and defaulting to 1.0 when unparseable (`graph_extractor.py` lines 160 to 163). There is no evidence quote and no per-entity confidence.
- Gleanings: after the first reply, the extractor sends `CONTINUE_PROMPT` ("MANY entities and relationships were missed in the last extraction ...") and, between rounds, `LOOP_PROMPT` asking for a single `Y` or `N` (`extract_graph.py` prompt module lines 128 to 129; loop in `graph_extractor.py` lines 99 to 121). `max_gleanings` defaults to 1 (`graphrag/config/defaults.py`). The paper forces the yes/no with "a logit bias of 100" and says gleaning "allows us to use larger chunk sizes without a drop in quality"; GPT-4 found "almost twice as many entity references" at 600-token chunks as at 2,400 (Section 3.1.2 and Appendix A.2, Figure 3). The paper used 600-token chunks with 100-token overlap. FilingIntel chunks are about 723 tokens, close to that size.
- The gleaning loop depends on free-form follow-up turns. With structured outputs it would be a second schema-constrained call with the first reply in context. No logit-bias parameter appears in the SDK's `messages.create` signature; a strict `{"more": boolean}` schema would do the same job as the forced yes/no (judgement, unverified).

### 4.4 Entity keys at extraction time

- GraphRAG: the key is the upper-cased name plus the type, merged on exact match (section 4.3).
- LangChain `LLMGraphTransformer` (`langchain_experimental/graph_transformers/llm.py`, https://github.com/langchain-ai/langchain-experimental): the prompt says "Node IDs should be names or human-readable identifiers found in the text" and asks for coreference resolution, using "the most complete identifier for that entity". `_format_nodes` then applies `id.title()` and `type.capitalize()`, and relationship types become upper-case with underscores (lines 596 to 614).
- neo4j-graphrag 1.22.0 resolves entities after loading: `SinglePropertyExactMatchResolver` merges nodes "with same label and exact same property (default is \"name\")", and the fuzzy and spaCy resolvers merge on text similarity with a default threshold of 0.8 (`src/neo4j_graphrag/components/resolver.py`, https://github.com/neo4j/neo4j-graphrag-python/tree/1.22.0).
- All three take the name from the model and normalize or match it in code. None asks the model for a stable key, and only the resolvers handle variants such as "Taiwan Semiconductor Manufacturing Company Limited" versus "TSMC".

## Implications for Step 8

1. **Model.** `claude-sonnet-5-5` as the default extractor, the same family the project already calls. Run `claude-haiku-5-5` on the same sample as a challenger; at a tenth of Sonnet's input price it could make gleaning or repeated runs affordable. Drop `claude-haiku-4-5-20251001`: legacy, no effort control, and its 4,096-token cache minimum rules out caching the system prompt. Keep Opus 5.5 and Fable 5.1 out unless the sample shows Sonnet failing.
2. **Effort and thinking.** Sweep `low`, `medium` and `high` on Sonnet 5.5 against a small hand-checked set of chunks, and record effort and output tokens per call. Start at `medium`. Test `thinking: {"type": "between_tools"}` as the low-cost setting; it is accepted only at `high` or below. Set `max_tokens` with room for thinking (8,000 to 16,000) and treat `stop_reason` `max_tokens` or `refusal` as a failed chunk, never as an empty result.
3. **Output mode.** `client.messages.parse(output_format=Model, output_config={"effort": ...})`, no tools. Keep every field required, so the schema stays well under 24 optional and 16 union-typed parameters. Use enums for label, edge type and confidence, and compare them case-insensitively. Do not put a "reasoning" field in the schema. Do not rely on `pattern`, `minimum` or `maxLength`; validate in code.
4. **Spans.** Check each `evidence_span` as an exact substring of `resolve(chunk_id)` (decide whether whitespace may be normalized), store its offsets, and drop and count the triples that fail. The drop count is a quality metric, as dropped sentences are for answers.
5. **Confidence.** A three-level enum, recorded as "uncalibrated" until a hand-checked sample maps levels to observed precision.
6. **Keys.** Build extracted-node keys in code from the label and a normalized name (casefold, collapse whitespace, strip punctuation), with `accession_no` added for RiskFactor and MetricValue per ADR-0004. Ask the model for the name as printed. Alias merging (TSMC) is a separate decision.
7. **Caching.** One explicit `cache_control` breakpoint at the end of the system prompt (ontology plus instructions), 5-minute TTL for sync runs. Keep schema, effort and thinking fixed within a run. Add a response cache keyed like `retrieve/response_cache.py` so reruns cost nothing.
8. **Batching.** Optional. It halves cost (Sonnet, one 10-K: about $0.50 saved at 500 output tokens) but adds up to 24 hours of latency, best-effort cache hits and a validation path without `parse`. Sync with caching is simpler for a 932-chunk run; revisit if the corpus grows or the challenger needs many runs.
9. **Gleaning.** Not in the first version. Measure recall on the sample first; one gleaning round roughly doubles cost.

## Open questions for /grill-with-docs

1. **Which models to compare?** Recommendation: Sonnet 5.5 (default) and Haiku 5.5 (challenger); not Haiku 4.5, Opus 5.5 or Fable 5.1.
2. **Effort and thinking.** Recommendation: sweep `low`, `medium`, `high` on Sonnet with and without `between_tools`, on 20 to 30 hand-checked chunks, and pick the cheapest setting within a stated recall loss. Write the threshold down before looking.
3. **Confidence form.** Recommendation: three-level enum. Alternative: a number checked in code to lie in [0, 1]. Either way it is uncalibrated until checked.
4. **Span matching.** Recommendation: exact substring after collapsing whitespace runs, with offsets stored. Ask whether curly versus straight quotes may also be normalized.
5. **Who builds keys?** Recommendation: code, from label and normalized name. Ask whether a small hand-written alias list (TSMC, Samsung) is in scope for Step 8 or deferred to Step 9.
6. **Batch or sync?** Recommendation: sync with prompt caching and a response cache; Batch only if run volume grows.
7. **Price table.** `retrieve/pricing.py` (dated 2026-10-05) has Sonnet 5.5 cache reads at $0.20; the pricing page now says $0.10. Recommendation: re-read the page and add a new dated table with Haiku 5.5, the 1-hour write rate and batch rates when Step 8 adds caching. Ask whether a changed price needs a BUILD-LOG line.
8. **Gleaning.** Recommendation: leave it out of the first version and measure recall without it.
9. **Rewrites and confidence** (from OPEN-DECISIONS): should a lower-confidence rewrite replace a higher-confidence value? Recommendation: keep the higher level and add the new evidence, so a rewrite never lowers what is stored.
10. **Claude token counts.** All counts here are `cl100k_base`. Recommendation: one `count_tokens` call on the real system prompt and a few chunks before the cost estimate goes in the spec.
