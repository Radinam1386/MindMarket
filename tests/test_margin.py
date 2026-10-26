import numpy as np
import pandas as pd
import pytest

from marketmind.backtest.margin import MarginConfig, run_margin

DAY = 86400


def _df(rows):
    """rows: list of (ts, open, high, low, close)"""
    return pd.DataFrame(
        {
            "ts": [r[0] for r in rows],
            "open": [r[1] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[3] for r in rows],
            "close": [r[4] for r in rows],
            "volume": [1.0] * len(rows),
        }
    )


def _sig(n, entry_at=None, exit_at=None, short_entry_at=None, short_exit_at=None):
    le = pd.Series([False] * n)
    lx = pd.Series([False] * n)
    se = pd.Series([False] * n)
    sx = pd.Series([False] * n)
    if entry_at is not None:
        le.iloc[entry_at] = True
    if exit_at is not None:
        lx.iloc[exit_at] = True
    if short_entry_at is not None:
        se.iloc[short_entry_at] = True
    if short_exit_at is not None:
        sx.iloc[short_exit_at] = True
    return le, lx, se, sx


BASE_TS = 1_700_000_000


def test_interest_accrues_per_4h_on_loan():
    # 4-day hold at 3x: loan = 3x collateral; interest = loan/6000 per 4h
    rows = [(BASE_TS, 100, 110, 99, 100)]
    rows += [(BASE_TS + d * DAY, 100, 100.1, 99.9, 100) for d in range(1, 6)]
    df = _df(rows)
    le, lx, se, sx = _sig(len(df), entry_at=0, exit_at=4)
    cfg = MarginConfig(loan_fee_rate=1 / 6000, trade_fee_rate=0.0)
    res = run_margin(df, le, lx, None, None, cfg=cfg)
    t = res.trades[0]
    hold_s = t.exit_ts - t.entry_ts
    assert hold_s == 4 * DAY
    loan = t.collateral * 3
    expected = (1 / 6000) * loan * ((4 * DAY) // 14400)
    assert t.interest == pytest.approx(expected, rel=1e-9)
    assert t.close_reason == "signal"


def test_liquidation_price_matches_measured_formula():
    # measured live: at 10x, liq at adverse 0.8546/10 - 0.0045 = 0.0810
    rows = [(BASE_TS, 100, 101, 99, 100)]
    rows += [(BASE_TS + DAY, 100, 101, 99, 100)]
    rows += [(BASE_TS + 2 * DAY, 95, 96, 91.5, 92)]  # low 91.5 < 91.90 -> liquidated
    rows += [(BASE_TS + 3 * DAY, 90, 91, 89, 90)]
    df = _df(rows)
    le, lx, se, sx = _sig(len(df), entry_at=0)
    cfg = MarginConfig(leverage=10, loan_fee_rate=0, trade_fee_rate=0)
    res = run_margin(df, le, lx, None, None, cfg=cfg)
    t = res.trades[0]
    assert t.close_reason == "liquidated"
    assert t.exit_px == pytest.approx(100 * (1 - 0.0810), abs=0.01)


def test_max_age_forced_close():
    rows = [(BASE_TS, 100, 101, 99, 100)]
    rows += [(BASE_TS + d * DAY, 100, 101, 99, 100) for d in range(1, 26)]
    df = _df(rows)
    le, lx, se, sx = _sig(len(df), entry_at=0)
    cfg = MarginConfig(loan_fee_rate=0, trade_fee_rate=0)
    res = run_margin(df, le, lx, None, None, cfg=cfg)
    t = res.trades[0]
    assert t.close_reason == "forced_age"
    assert t.hold_days == pytest.approx(21.0)


def test_no_lookahead_entry_at_next_open():
    rows = [(BASE_TS + i * DAY, 100, 101, 99, 100 + i) for i in range(4)]
    df = _df(rows)
    le, lx, se, sx = _sig(len(df), entry_at=len(df) - 1)
    res = run_margin(df, le, lx, None, None)
    assert len(res.trades) == 0


def test_short_side_pnl_zero_fees():
    rows = [(BASE_TS, 100, 101, 99, 100)]
    rows += [(BASE_TS + DAY, 100, 101, 99, 100)]
    rows += [(BASE_TS + 2 * DAY, 90, 91, 89, 90)]
    rows += [(BASE_TS + 3 * DAY, 90, 91, 89, 90)]
    df = _df(rows)
    le, lx, se, sx = _sig(len(df), short_entry_at=0, short_exit_at=2)
    cfg = MarginConfig(loan_fee_rate=0, trade_fee_rate=0, leverage=1)
    res = run_margin(df, le, lx, se, sx, cfg=cfg)
    t = res.trades[0]
    assert t.side == "short"
    # notional = collateral*(1+1); price 100->90 => +10% on notional = +20% on collateral at 1x "leverage"
    assert t.return_pct == pytest.approx(20.0)


def test_fee_stress_doubles_costs():
    def run_with(stress):
        rows = [(BASE_TS, 100, 101, 99, 100)]
        rows += [(BASE_TS + DAY, 100, 101, 99, 100)]
        rows += [(BASE_TS + 2 * DAY, 110, 111, 109, 110)]
        rows += [(BASE_TS + 3 * DAY, 110, 111, 109, 110)]
        df = _df(rows)
        le, lx, se, sx = _sig(len(df), entry_at=0, exit_at=2)
        cfg = MarginConfig(leverage=3, fee_stress=stress)
        return run_margin(df, le, lx, None, None, cfg=cfg).trades[0]

    t1, t2 = run_with(1.0), run_with(2.0)
    assert t2.total_fees == pytest.approx(2 * t1.total_fees, rel=1e-9)


def test_real_fee_match_dry_run_observation():
    # observed 2026-10-25: collateral 100, 3x, entry 80000:
    # trade_fee 0.75 (0.25% of loan), open_fee 0.05 (loan/6000), extend 0.05 per 4h
    rows = [(BASE_TS, 80000, 80100, 79900, 80000)]
    rows += [(BASE_TS + 4 * 3600, 80000, 80100, 79900, 80000)]   # entry at open (1 renewal period hold)
    rows += [(BASE_TS + 8 * 3600, 80000, 80100, 79900, 80000)]   # exit signal on this close
    rows += [(BASE_TS + 12 * 3600, 80000, 80100, 79900, 80000)]  # exit executes at open
    df = _df(rows)
    le, lx, se, sx = _sig(len(df), entry_at=0, exit_at=1)
    res = run_margin(df, le, lx, None, None, cfg=MarginConfig(leverage=3))
    t = res.trades[0]
    assert t.exit_ts - t.entry_ts == 4 * 3600  # exactly one renewal period
    loan = t.collateral * 3
    # fees = 2 legs x 0.25% x loan + activation loan/6000 + 1 renewal x loan/6000
    expected = 2 * 0.0025 * loan + 2 * (loan / 6000)
    assert t.total_fees == pytest.approx(expected, rel=1e-9)


# ---------------- sizing (E006) ----------------
from marketmind.backtest.margin import SizingConfig


def test_fractional_sizing_one_liquidation_is_not_ruin():
    # one -35% crash day (liquidates a 3x long), then recovery; account must survive
    rows = [(BASE_TS, 100, 101, 99, 100)]
    rows += [(BASE_TS + DAY, 100, 101, 60, 62)]          # crash -> liquidation
    rows += [(BASE_TS + 2 * DAY, 62, 96.1, 61, 96)]       # +55% recovery
    rows += [(BASE_TS + d * DAY, 96, 97, 95, 96) for d in range(3, 30)]
    df = _df(rows)
    le = pd.Series([True] * len(df))                      # always want long when flat
    lx = pd.Series([False] * len(df))
    cfg = MarginConfig(leverage=3, loan_fee_rate=0, trade_fee_rate=0)
    sz = SizingConfig(fraction_of_cash=0.15)
    res = run_margin(df, le, lx, None, None, cfg=cfg, sizing=sz)
    assert res.metrics["closes"].get("liquidated", 0) == 1
    # the liquidation costs ~17% of equity (0.15 x ~1.12), not the account
    eq = res.equity.to_numpy()
    assert eq[:4].min() > 0.60 * eq[0]
    # account keeps trading afterwards and ends above 85% of start
    assert len(res.trades) >= 2
    assert eq[-1] > 0.85 * eq[0]
    # collateral never exceeds 15% of the equity just before entry
    ts_arr = df["ts"].to_numpy()
    eq_arr = res.equity.to_numpy()
    for t in res.trades:
        i = int(np.searchsorted(ts_arr, t.entry_ts))
        prior = eq_arr[i - 1] if i > 0 else eq_arr[0]
        assert t.collateral <= 0.1501 * prior


def test_notional_cap_enforced():
    rows = [(BASE_TS, 100, 101, 99, 100)]
    rows += [(BASE_TS + d * DAY, 100, 101, 99, 100) for d in range(1, 4)]
    df = _df(rows)
    le, lx, se, sx = _sig(len(df), entry_at=0, exit_at=3)
    cfg = MarginConfig(leverage=3, loan_fee_rate=0, trade_fee_rate=0)
    sz = SizingConfig(max_notional=1700.0, min_notional=100.0)
    res = run_margin(df, le, lx, None, None, cfg=cfg, sizing=sz, initial_cash=1_000_000.0)
    t = res.trades[0]
    assert t.notional == pytest.approx(1700.0, rel=1e-6)      # capped exactly
    assert t.collateral == pytest.approx(1700.0 / 4, rel=1e-6)


def test_min_notional_skips_tiny_entries():
    rows = [(BASE_TS, 100, 101, 99, 100)]
    rows += [(BASE_TS + d * DAY, 100, 101, 99, 100) for d in range(1, 4)]
    df = _df(rows)
    le, lx, se, sx = _sig(len(df), entry_at=0, exit_at=3)
    cfg = MarginConfig(leverage=3, loan_fee_rate=0, trade_fee_rate=0)
    sz = SizingConfig(max_notional=1700.0, min_notional=100.0)
    # 20 cash at 3x -> notional 80 < min 100 -> entry must be skipped
    res = run_margin(df, le, lx, None, None, cfg=cfg, sizing=sz, initial_cash=20.0)
    assert len(res.trades) == 0


def test_vol_target_reduces_size_in_high_vol():
    def run_with(rows):
        df = _df(rows)
        le, lx, se, sx = _sig(len(df), entry_at=6, exit_at=7)  # after ATR(5) warm-up
        cfg = MarginConfig(leverage=3, loan_fee_rate=0, trade_fee_rate=0)
        sz = SizingConfig(vol_target_risk=0.02, atr_n=5, fraction_of_cash=1.0)
        res = run_margin(df, le, lx, None, None, cfg=cfg, sizing=sz, initial_cash=1_000_000.0)
        return res.trades[0].collateral

    calm = [(BASE_TS + d * DAY, 100, 101, 99, 100) for d in range(8)]
    wild = [(BASE_TS + d * DAY, 100, 110, 90, 100) for d in range(8)]
    assert run_with(wild) < run_with(calm)
