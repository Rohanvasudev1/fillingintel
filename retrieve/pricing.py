"""The dated price tables that cost figures come from (Steps 5 and 8).

Every cost is computed from the token counts the API reported and the prices
in one table, read from each vendor's pricing page on the table's date.  The
table goes into every results header and run report, so a cost figure can be
traced and recomputed.  A table is never edited after use: a changed price is a
new table, and past costs keep the table they used.  Voyage costs are list
prices, before the account's free-token allowance.

``TABLE_2026_10_05`` serves Step 5 to 7 runs and stays the default.
``TABLE_2026_10_09`` corrects Sonnet 5.5 cache reads to $0.10 per MTok
(docs/research/claude-extraction-structured-output.md, section 2) and is used
by extraction, the first caller that reads the prompt cache.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Protocol

PRICE_TABLE_DATE = "2026-10-05"
_TOKENS_PER_MTOK = 1_000_000
_ANTHROPIC_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
_VOYAGE_SOURCE = "https://docs.voyageai.com/docs/pricing"
_OPENAI_SOURCE = "https://developers.openai.com/api/docs/pricing"  # Standard, short context


@dataclass(frozen=True)
class TokenPrice:
    """USD per million tokens.  Anthropic cache writes are the 5-minute rate."""

    input_per_mtok: float
    source: str
    output_per_mtok: float = 0.0
    cache_write_per_mtok: float = 0.0
    cache_read_per_mtok: float = 0.0


PRICES = MappingProxyType({
    "claude-sonnet-5-5": TokenPrice(2.00, _ANTHROPIC_SOURCE, output_per_mtok=10.00,
                                    cache_write_per_mtok=2.50, cache_read_per_mtok=0.20),
    "gpt-6-luna": TokenPrice(0.10, _OPENAI_SOURCE, output_per_mtok=0.50,
                             cache_write_per_mtok=0.125, cache_read_per_mtok=0.01),
    "gpt-6-sol": TokenPrice(2.00, _OPENAI_SOURCE, output_per_mtok=10.00,
                            cache_write_per_mtok=2.50, cache_read_per_mtok=0.20),
    "voyage-4-large": TokenPrice(0.12, _VOYAGE_SOURCE),
})


@dataclass(frozen=True)
class PriceTable:
    """The prices read on *date*, by model."""

    date: str
    prices: Mapping[str, TokenPrice]

    def price(self, model: str) -> TokenPrice:
        try:
            return self.prices[model]
        except KeyError:
            raise ValueError(f"no price for {model!r} in the {self.date} table") from None


TABLE_2026_10_05 = PriceTable(PRICE_TABLE_DATE, PRICES)
TABLE_2026_10_09 = PriceTable("2026-10-09", MappingProxyType({
    "claude-sonnet-5-5": TokenPrice(2.00, _ANTHROPIC_SOURCE, output_per_mtok=10.00,
                                    cache_write_per_mtok=2.50, cache_read_per_mtok=0.10),
}))


class AnthropicUsage(Protocol):
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int


class OpenAIReportedUsage(Protocol):
    input_tokens: int  # includes the cached and cache-write tokens
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int  # includes the reasoning tokens


def _price(model: str) -> TokenPrice:
    return TABLE_2026_10_05.price(model)


def anthropic_cost(model: str, usage: AnthropicUsage,
                   table: PriceTable = TABLE_2026_10_05) -> float:
    """USD for one Messages API call, from the usage it reported and *table*'s prices."""
    p = table.price(model)
    return (
        usage.input_tokens * p.input_per_mtok
        + usage.output_tokens * p.output_per_mtok
        + usage.cache_read_input_tokens * p.cache_read_per_mtok
        + usage.cache_creation_input_tokens * p.cache_write_per_mtok
    ) / _TOKENS_PER_MTOK


def openai_cost(model: str, usage: OpenAIReportedUsage) -> float:
    """USD for one Responses API call, from the usage it reported.

    OpenAI's input count includes cached and cache-write tokens, so they are
    taken out and billed at their own rates.  Reasoning tokens are already in
    the output count and are billed as output.
    """
    p = _price(model)
    plain_input = usage.input_tokens - usage.cached_input_tokens - usage.cache_write_tokens
    return (
        plain_input * p.input_per_mtok
        + usage.cached_input_tokens * p.cache_read_per_mtok
        + usage.cache_write_tokens * p.cache_write_per_mtok
        + usage.output_tokens * p.output_per_mtok
    ) / _TOKENS_PER_MTOK


def embedding_cost(model: str, tokens: int) -> float:
    """USD for embedding *tokens* tokens with *model*."""
    return tokens * _price(model).input_per_mtok / _TOKENS_PER_MTOK


def price_table(models: Iterable[str],
                table: PriceTable = TABLE_2026_10_05) -> dict[str, object]:
    """The prices of *models* in *table*, with the table's date, for a results header."""
    return {"date": table.date, "unit": "USD per million tokens",
            "models": {m: asdict(table.price(m)) for m in dict.fromkeys(models)}}
