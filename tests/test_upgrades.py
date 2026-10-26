import time

import numpy as np
import pandas as pd
import pytest

from marketmind.backtest.validation import segment_metrics
from marketmind.paper import state
from marketmind.paper.runner import entry_fill, exit_fill


def test_segment_metrics_sharpe_sane():
    """Regression: pandas-3 asi8 unit bug inflated Sharpe ~31x (0.88 -> 27.8)."""
    idx = pd.date_range("2024-01-01", periods=365, freq="D", tz="UTC")
    rng = np.random.default_rng(11)
    eq = pd.Series(np.cumprod(1 + rng.normal(0.0003, 0.01, 365)), index=idx)
    m = segment_metrics(eq)
    daily = eq.pct_change().dropna()
    expected = daily.mean() / daily.std() * np.sqrt(365.25)
    assert abs(m["sharpe"] - expected) < 0.05
    assert m["sharpe"] < 3  # a 10%-vol daily series cannot have Sharpe 20+
    assert "calmar" in m


def test_fills_respect_bid_ask_plus_slippage():
    q = {"bid": 100.0, "ask": 100.4, "mid": 100.2, "last": 100.2}
    assert entry_fill(q) == pytest.approx(100.4 * 1.0005, rel=1e-9)
    assert exit_fill(q) == pytest.approx(100.0 * 0.9995, rel=1e-9)
    assert entry_fill(q) > exit_fill(q)  # spread+slippage always costs us


def _fresh_db(tmp_path):
    state.DB_PATH = tmp_path / "paper.sqlite"
    conn = state.connect()
    state.init_account(cash=200.0)
    return conn


def test_peak_equity_and_analyst_helpers(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "p.sqlite")
    conn = state.connect()
    state.init_account(cash=200.0)
    now = int(time.time())
    for v, off in ((210.0, 3000), (205.0, 2000), (190.0, 1000)):
        conn.execute("INSERT INTO equity (ts, value) VALUES (?,?)", (now - off, v))
    conn.commit()
    assert state.peak_equity(conn) == 210.0

    state.save_analyst(conn, "BTCUSDT", {"bias": "bearish", "confidence": 0.6,
                                         "entry_ok": False, "size_factor": 0.4,
                                         "key_levels": {"support": 70000, "resistance": 81000},
                                         "summary": "تست"})
    assert state.analyst_today(conn, "BTCUSDT") is True
    hist = state.recent_analyst(conn, "BTCUSDT", 3)
    assert len(hist) == 1 and hist[0]["bias"] == "bearish"
    conn.close()


def test_idempotency_guard_data(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "p2.sqlite")
    conn = state.connect()
    state.init_account(cash=200.0)
    assert state.last_cycle_day(conn) is None
    state.record_decision(conn, "BTCUSDT", "hold", {"px": 1})
    assert state.last_cycle_day(conn) is not None
    assert int(time.time()) - state.last_cycle_day(conn) < 20 * 3600  # guard would trigger
    conn.close()
