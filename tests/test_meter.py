import pytest

from usage_weights.meter import CLI_METER_KEYS, percent_from_cli_fraction, reading_bounds


def test_bounds():
    assert reading_bounds(63, "cli") == (63, 64)
    assert reading_bounds(63, "usage_endpoint") == (62.5, 63.5)
    with pytest.raises(ValueError):
        reading_bounds(63, "other")


def test_both_readers_give_half_point_steps():
    lo = max(reading_bounds(63, "cli")[0], reading_bounds(63, "usage_endpoint")[0])
    hi = min(reading_bounds(63, "cli")[1], reading_bounds(63, "usage_endpoint")[1])
    assert (lo, hi) == (63, 63.5)


def test_percent_from_fraction():
    for x, p in [(0.63, 63), (0.29, 29), (0.57, 57), (1.04, 104), (0.0, 0)]:
        assert percent_from_cli_fraction(x) == p


def test_percent_rejects():
    for x in [-0.01, 11.0, "0.5", None]:
        with pytest.raises(ValueError):
            percent_from_cli_fraction(x)


def test_cli_keys():
    assert CLI_METER_KEYS == {"all_models": "seven_day", "fable": "seven_day_overage_included"}
