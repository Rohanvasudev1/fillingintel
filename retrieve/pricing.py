"""The dated price table that cost figures come from (Step 5).

Every cost is computed from the token counts the API reported and the prices
below, read from each vendor's pricing page on ``PRICE_TABLE_DATE``.  The table
goes into every results header, so a cost figure can be traced and recomputed.
Voyage costs are list prices, before the account's free-token allowance.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Protocol

PRICE_TABLE_DATE = "2026-10-05"
_TOKENS_PER_MTOK = 1_000_000
_ANTHROPIC_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
_VOYAGE_SOURCE = "https://docs.voyageai.com/docs/pricing"


@dataclass(frozen=True)
class TokenPrice:
    """USD per million tokens.  Cache writes are the 5-minute rate."""

    input_per_mtok: float
    source: str
    output_per_mtok: float = 0.0
    cache_write_per_mtok: float = 0.0
    cache_read_per_mtok: float = 0.0


PRICES = MappingProxyType({
    "claude-sonnet-5-5": TokenPrice(2.00, _ANTHROPIC_SOURCE, output_per_mtok=10.00,
                                    cache_write_per_mtok=2.50, cache_read_per_mtok=0.20),
    "claude-opus-5-5": TokenPrice(4.00, _ANTHROPIC_SOURCE, output_per_mtok=20.00,
                                  cache_write_per_mtok=5.00, cache_read_per_mtok=0.20),
    "voyage-4-large": TokenPrice(0.12, _VOYAGE_SOURCE),
})


class AnthropicUsage(Protocol):
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int


def _price(model: str) -> TokenPrice:
    try:
        return PRICES[model]
    except KeyError:
        raise ValueError(f"no price for {model!r} in the {PRICE_TABLE_DATE} table") from None


def anthropic_cost(model: str, usage: AnthropicUsage) -> float:
    """USD for one Messages API call, from the usage it reported."""
    p = _price(model)
    return (
        usage.input_tokens * p.input_per_mtok
        + usage.output_tokens * p.output_per_mtok
        + usage.cache_read_input_tokens * p.cache_read_per_mtok
        + usage.cache_creation_input_tokens * p.cache_write_per_mtok
    ) / _TOKENS_PER_MTOK


def embedding_cost(model: str, tokens: int) -> float:
    """USD for embedding *tokens* tokens with *model*."""
    return tokens * _price(model).input_per_mtok / _TOKENS_PER_MTOK


def price_table(models: Iterable[str]) -> dict[str, object]:
    """The prices of *models*, with the table's date, for a results header."""
    return {"date": PRICE_TABLE_DATE, "unit": "USD per million tokens",
            "models": {m: asdict(_price(m)) for m in dict.fromkeys(models)}}
