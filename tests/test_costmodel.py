from marketmind.analytics.costmodel import (
    breakeven_table,
    headline_table,
    renewal_periods,
    round_trip_cost,
)


def test_renewal_periods():
    assert renewal_periods(4) == 1
    assert renewal_periods(3) == 0
    assert renewal_periods(5) == 1
    assert renewal_periods(24) == 6
    assert renewal_periods(504) == 126  # 21 days = max position age


def test_round_trip_cost_matches_measured_dry_run():
    # observed 2026-10-25: collateral 100, 3x, 4h hold, flat price:
    # fees = 0.75 + 0.75 (trade legs) + 0.05 (activation) + 0.05 (1 renewal) = 1.60 USDT
    # on notional 400 -> 0.40%
    cost = round_trip_cost(3, 4)
    assert abs(cost - 0.004) < 5e-4


def test_cost_grows_with_horizon_and_leverage():
    tbl = breakeven_table()
    real = tbl[tbl.fee_stress == "real"]
    for lev in (2, 3, 5, 10):
        sub = real[real.leverage == lev].sort_values("hold_h")
        assert sub["price_move_needed_pct"].tolist() == sorted(sub["price_move_needed_pct"].tolist())
    # higher leverage -> higher per-notional cost (loan share grows)
    day1 = real[real.horizon == "1d"].set_index("leverage")["price_move_needed_pct"]
    assert day1.loc[10] > day1.loc[5] > day1.loc[2]


def test_fee_stress_scaling():
    base = round_trip_cost(3, 24, fee_stress=1.0)
    doubled = round_trip_cost(3, 24, fee_stress=2.0)
    assert abs(doubled - 2 * base) < 1e-12


def test_headline_covers_horizons():
    h = headline_table()
    assert set(h["horizon"]) == set(["4h", "12h", "1d", "3d", "7d", "14d", "21d"])
    assert len(h) == 7 * 3
