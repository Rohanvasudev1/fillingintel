"""Builders for eval-set tests: real fixture chunks, so gold IDs resolve."""
from eval.corpus_index import ChunkIndex
from eval.schema import EvalRecord, split_for
from ingest.parsed_files import text_sha256

DEFAULT_QUESTION = "What was the company's total revenue for the fiscal year?"


def make_record(index: ChunkIndex, **overrides) -> EvalRecord:
    """A valid ``lookup`` record whose gold chunk is the index's first chunk."""
    gold = overrides.pop("gold_chunk_ids", [index.chunk_ids()[0]])
    question = overrides.get("question", DEFAULT_QUESTION)
    negatives = overrides.pop("hard_negatives", _default_negatives(index, gold))
    fields = {
        "id": "q0001",
        "question": question,
        "class": "lookup",
        "gold_answer": "As stated in the cited passage.",
        "gold_chunk_ids": gold,
        "gold_text_sha256": {c: text_sha256(index.text(c)) for c in gold},
        "hard_negatives": negatives,
        "tickers": [index.ticker(gold[0])] if gold else ["NVDA"],
        "fiscal_periods": [index.fiscal_period(gold[0])] if gold else ["FY2026"],
        "topic": "financials",
        "difficulty": "easy",
        "reasoning": "quantitative",
        "evidence_scope": "single",
        "provenance": "human_written",
        "author": "tester",
        "split": split_for(question),
        "notes": "",
    }
    fields.update(overrides)
    return EvalRecord.model_validate(fields)


def _default_negatives(index: ChunkIndex, gold: list[str]) -> list[dict[str, str]]:
    """One valid same_company_other_period negative for answerable records."""
    if not gold:
        return []
    first = index.info(gold[0])
    for chunk_id in index.chunk_ids():
        info = index.info(chunk_id)
        if info and info.ticker == first.ticker and info.fiscal_period != first.fiscal_period:
            return [{"chunk_id": chunk_id, "relation": "same_company_other_period"}]
    return []
