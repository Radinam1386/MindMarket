import numpy as np
import pandas as pd

from marketmind.backtest import baselines
from marketmind.backtest.engine import run


def _df(closes):
    closes = np.asarray(closes, float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    n = len(closes)
    return pd.DataFrame({
        "ts": np.arange(n, dtype=np.int64) * 86400,
        "open": opens,
        "high": np.maximum(opens, closes) * 1.001,
        "low": np.minimum(opens, closes) * 0.999,
        "close": closes,
        "volume": np.full(n, 10.0),
    })


def test_long_trade_fees_and_next_open_execution():
    # trend: flat, breakout up, keep rising, then exit signal
    closes = [100, 100, 100, 110, 120, 130, 120, 110]
    df = _df(closes)
    sig = baselines.donchian(df, entry_n=2, exit_n=2)
    res = run(df, sig["long_entry"], sig["long_exit"], fee_open=0.003, fee_close=0.003)
    assert len(res.trades) >= 1
    t = res.trades[0]
    # entry executes at the open AFTER the signal bar close, never same-bar
    assert t.entry_ts > df["ts"].iloc[0]
    expected_ret = ((t.exit_px * (1 - 0.003)) / (t.entry_px * (1 + 0.003)) - 1) * 100
    assert abs(t.return_pct - expected_ret) < 1e-9


def test_no_lookahead_entry_uses_next_open():
    closes = [100, 100, 100, 110, 120, 130]
    df = _df(closes)
    n = len(df)
    long_entry = pd.Series([False] * (n - 1) + [True])  # signal on the LAST bar
    long_exit = pd.Series([False] * n)
    res = run(df, long_entry, long_exit, fee_open=0.003, fee_close=0.003)
    # signal on last bar can only be executed at a next open that never comes:
    # position must not be opened
    assert len(res.trades) == 0


def test_short_side_pnl_math():
    closes = [100, 100, 100, 95, 90, 85, 90, 100]  # dip then recover
    df = _df(closes)
    n = len(df)
    short_entry = pd.Series([False, False, True] + [False] * (n - 3))
    short_exit = pd.Series([False] * 6 + [True, False])
    res = run(df, None_or_false(n), None_or_false(n), short_entry, short_exit,
              fee_open=0.003, fee_close=0.003)
    t = [x for x in res.trades if x.side == "short"]
    assert len(t) == 1
    tr = t[0]
    # opens follow previous close: entry at open[3]=100, exit at open[7]=90
    assert tr.entry_px == 100.0 and tr.exit_px == 90.0
    expected = ((100 - 90) - 100 * 0.003 - 90 * 0.003) / 100 * 100
    assert abs(tr.return_pct - expected) < 1e-9


def None_or_false(n):
    return pd.Series([False] * n)


def test_donchian_strict_breakout_boundary():
    closes = [100] * 5 + [100]
    highs = [100] * 6
    df2 = pd.DataFrame({
        "ts": np.arange(6) * 86400, "open": closes, "high": highs,
        "low": [c - 1 for c in closes], "close": closes, "volume": [1.0] * 6,
    })
    sig2 = baselines.donchian(df2, entry_n=3, exit_n=3)
    assert not sig2["long_entry"].iloc[-1], "close == prior channel high must NOT trigger (strict >)"

    closes = [100] * 5 + [100.0001]
    df3 = pd.DataFrame({
        "ts": np.arange(6) * 86400, "open": closes, "high": closes,
        "low": [c - 1 for c in closes], "close": closes, "volume": [1.0] * 6,
    })
    sig3 = baselines.donchian(df3, entry_n=3, exit_n=3)
    assert sig3["long_entry"].iloc[-1], "any close strictly above prior-3-bar highs must trigger"
