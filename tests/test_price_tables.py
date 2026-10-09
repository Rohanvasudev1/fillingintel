"""The dated price tables: a new one for Step 8, the old one unchanged (Step 8, ticket 03)."""
import pytest

from retrieve.answer_model import TokenUsage
from retrieve.pricing import (
    PRICE_TABLE_DATE,
    TABLE_2026_10_05,
    TABLE_2026_10_09,
    anthropic_cost,
    price_table,
)

SONNET = "claude-sonnet-5-5"
USAGE = TokenUsage(input_tokens=1000, output_tokens=500, cache_read_input_tokens=2000,
                   cache_creation_input_tokens=400)


def test_the_new_table_prices_sonnet_cache_reads_at_ten_cents():
    assert TABLE_2026_10_09.date == "2026-10-09"
    assert anthropic_cost(SONNET, USAGE, TABLE_2026_10_09) == pytest.approx(
        (1000 * 2.00 + 500 * 10.00 + 2000 * 0.10 + 400 * 2.50) / 1_000_000
    )


def test_the_old_table_and_the_default_cost_are_unchanged():
    assert TABLE_2026_10_05.date == PRICE_TABLE_DATE == "2026-10-05"
    old = (1000 * 2.00 + 500 * 10.00 + 2000 * 0.20 + 400 * 2.50) / 1_000_000
    assert anthropic_cost(SONNET, USAGE) == pytest.approx(old)
    assert anthropic_cost(SONNET, USAGE, TABLE_2026_10_05) == pytest.approx(old)


def test_a_header_names_the_table_it_used():
    assert price_table([SONNET], TABLE_2026_10_09)["date"] == "2026-10-09"
    assert price_table([SONNET], TABLE_2026_10_09)["models"][SONNET]["cache_read_per_mtok"] == 0.10
    assert price_table([SONNET])["date"] == "2026-10-05"


def test_a_model_missing_from_the_new_table_is_refused():
    with pytest.raises(ValueError, match="2026-10-09"):
        anthropic_cost("gpt-6-luna", USAGE, TABLE_2026_10_09)
