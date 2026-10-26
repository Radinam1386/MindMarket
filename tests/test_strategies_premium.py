import numpy as np
import pandas as pd
import pytest

from marketmind.backtest.strategies import _bars_since, _anchor_value, donchian_er, efficiency_ratio, vol_squeeze
from marketmind.analytics.premium import bucket_table, load_pair


def _df(closes, high_pad=1.01, low_pad=0.99):
    closes = np.asarray(closes, float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    n = len(closes)
    return pd.DataFrame({
        "ts": np.arange(n, dtype=np.int64) * 86400,
        "open": opens, "high": closes * high_pad, "low": closes * low_pad,
        "close": closes, "volume": np.ones(n),
    })


def test_efficiency_ratio_bounds():
    trending = _df(np.linspace(100, 200, 60))          # perfectly linear
    choppy = _df(100 + 10 * np.sin(np.arange(60) * 1.0))  # oscillating
    er_t = efficiency_ratio(trending["close"], 20).iloc[-1]
    er_c = efficiency_ratio(choppy["close"], 20).iloc[-1]
    assert er_t > 0.9
    assert er_c < er_t


def test_donchian_er_gate_blocks_choppy_entries():
    # oscillating price crosses the channel constantly; ER gate must suppress entries
    choppy = _df(100 + 8 * np.sin(np.arange(120) * 0.5), high_pad=1.02, low_pad=0.98)
    sig_gated = donchian_er(choppy, entry_n=10, exit_n=5, er_n=20, er_min=0.45)
    sig_plain = donchian_er(choppy, entry_n=10, exit_n=5, er_n=20, er_min=0.0)
    assert sig_gated["long_entry"].sum() < sig_plain["long_entry"].sum()


def test_vol_squeeze_fires_on_compression_breakout():
    # 100 quiet days (tiny noise), then a breakout day
    rng = np.random.default_rng(3)
    quiet = 100 + rng.normal(0, 0.1, 100)
    prices = np.concatenate([quiet, [110.0], [111.0, 112.0, 111.5, 112.5, 113.0, 112.0, 113.5]])
    df = _df(prices)
    sig = vol_squeeze(df, bb_n=20, squeeze_pct_window=60, break_n=10,
                      time_stop_bars=5, atr_n=14)
    assert sig["long_entry"].sum() >= 1, "breakout after compression must trigger"
    # after entry, time stop must fire within ~5 bars
    entries = np.where(sig["long_entry"].to_numpy())[0]
    e = entries[0]
    assert sig["long_exit"].iloc[e:e + 7].any()


def test_bars_since_and_anchor():
    sig = pd.Series([False, True, False, False, False, True, False])
    bs = _bars_since(sig)
    assert bs.tolist() == [10**6, 0, 1, 2, 3, 0, 1]
    vals = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    anchored = _anchor_value(sig, vals)
    assert anchored.tolist()[1:6] == [2.0, 2.0, 2.0, 2.0, 6.0]


def test_premium_bucket_table_shapes():
    n = 400
    rng = np.random.default_rng(5)
    ts = np.arange(n, dtype=np.int64) * 86400
    prem = pd.DataFrame({"ts": ts, "close": 50000 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, n)))})
    btc = pd.DataFrame({"ts": ts, "close": 30000 * np.exp(np.cumsum(rng.normal(0.0008, 0.03, n)))})
    df = load_pair(prem, btc)
    tbl = bucket_table(df)
    assert set(tbl["bucket"]) == {"z<-2", "-2..-1", "-1..1", "1..2", "z>2"}
    assert tbl["n_days"].sum() > 0
    n_mid = int(tbl.loc[tbl["bucket"] == "-1..1", "n_days"].iloc[0])
    n_z2 = int(tbl.loc[tbl["bucket"] == "z>2", "n_days"].iloc[0])
    assert n_mid > n_z2  # middle bucket dominates in roughly-normal z
