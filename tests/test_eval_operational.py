"""Percentiles for the latency report (Step 5 ticket 05)."""
import pytest

from eval.operational import percentile


def test_percentiles_interpolate_between_the_closest_ranks():
    values = [40.0, 10.0, 30.0, 20.0]  # order does not matter
    assert percentile(values, 0.5) == pytest.approx(25.0)
    assert percentile(values, 0.95) == pytest.approx(30.0 + 0.85 * 10.0)
    assert (percentile(values, 0.0), percentile(values, 1.0)) == (10.0, 40.0)


def test_one_value_is_every_percentile():
    assert percentile([7.5], 0.5) == percentile([7.5], 0.95) == 7.5


def test_no_values_or_a_bad_quantile_is_refused():
    with pytest.raises(ValueError, match="no values"):
        percentile([], 0.5)
    with pytest.raises(ValueError, match="between 0 and 1"):
        percentile([1.0], 95)
