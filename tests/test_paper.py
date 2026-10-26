import json
import time

import pytest

from marketmind.paper import state
from marketmind.paper.runner import sizing


def test_open_close_accounting_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "paper.sqlite")
    state.init_account(cash=200.0)
    conn = state.connect()
    assert state.account_cash(conn) == 200.0

    pid = state.open_position(conn, "BTCUSDT", "long", price=80000.0, collateral=40.0,
                              leverage=3.0, liq_frac=0.8546, liq_offset=0.0045)
    assert state.account_cash(conn) == pytest.approx(160.0)  # collateral deducted once

    # flat 7 days later at same price: fees only
    pos = conn.execute("SELECT * FROM positions WHERE id=?", (pid,)).fetchone()
    pos = _with_entry_ts(pos, int(time.time()) - 7 * 86400)
    interest, fees, settlement = state.position_fees_and_settlement(pos, 80000.0,
                                                                    int(time.time()), "signal")
    loan = 120.0
    expected_renewals = 7 * 24 // 4
    assert interest == pytest.approx(loan / 6000 * expected_renewals, rel=1e-9)
    assert fees == pytest.approx(2 * 0.0025 * loan + loan / 6000 + interest, rel=1e-9)
    assert settlement == pytest.approx(40.0 - fees, rel=1e-9)  # flat price: lose fees only


def test_equity_snapshot_marks_positions(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "paper.sqlite")
    state.init_account(cash=200.0)
    conn = state.connect()
    state.open_position(conn, "BTCUSDT", "long", price=80000.0, collateral=40.0,
                        leverage=3.0, liq_frac=0.8546, liq_offset=0.0045)
    # price +10% -> qty*(88000-80000); qty = 160/80000 = 0.002
    eq = state.snapshot_equity(conn, {"BTCUSDT": 88000.0})
    gross = 0.002 * 8000.0
    loan = 120.0
    fees_now = 2 * 0.0025 * loan + loan / 6000  # legs + activation; 0 renewals yet
    assert eq == pytest.approx(160.0 + 40.0 + gross - fees_now, rel=1e-6)


def test_sizing_bounds():
    # 20% cap of 200 = 40; at 3x notional 160 within [100, 1700]
    assert sizing(200.0, 80000.0, atr=1600.0) == pytest.approx(40.0, rel=1e-9)
    # huge ATR -> vol-target shrinks below min notional -> skip (0)
    assert sizing(200.0, 80000.0, atr=30000.0) == 0.0


def _with_entry_ts(row, ts):
    d = dict(row)
    d["entry_ts"] = ts
    return d


def test_recent_analyst_exposes_cached_opinion_keys(tmp_path, monkeypatch):
    """Regression: run_cycle's cached-opinion branch (second same-day cycle) reads
    size_factor/support/resistance from recent_analyst rows; any missing key used
    to crash the whole cycle with KeyError."""
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "paper.sqlite")
    conn = state.connect()
    state.save_analyst(conn, "BTCUSDT",
                       {"bias": "bullish", "confidence": 0.8, "entry_ok": True,
                        "size_factor": 0.5, "ok": True,
                        "key_levels": {"support": "60k", "resistance": "70k"},
                        "summary": "ok"})
    rows = state.recent_analyst(conn, "BTCUSDT", 1)
    assert rows, "analyst row must be readable"
    missing = ({"ts", "bias", "confidence", "entry_ok", "size_factor",
                "support", "resistance", "summary"} - set(rows[0]))
    assert not missing, f"recent_analyst is missing keys: {sorted(missing)}"
    assert rows[0]["size_factor"] == pytest.approx(0.5)
    assert rows[0]["support"] == "60k" and rows[0]["resistance"] == "70k"


def test_short_open_close_accounting(tmp_path, monkeypatch):
    """Short side: collateral math, liquidation px above entry, and PnL when
    price falls — the runner opens these when MARKETMIND_ALLOW_SHORTS=1."""
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "paper.sqlite")
    state.init_account(cash=200.0)
    conn = state.connect()
    pid = state.open_position(conn, "BTCUSDT", "short", price=80000.0, collateral=40.0,
                              leverage=3.0, liq_frac=0.8546, liq_offset=0.0045)
    assert state.account_cash(conn) == pytest.approx(160.0)
    pos = conn.execute("SELECT * FROM positions WHERE id=?", (pid,)).fetchone()
    assert pos["side"] == "short"
    assert pos["liq_px"] == pytest.approx(80000.0 * (1 + 0.8546 / 3.0 - 0.0045), rel=1e-9)
    # price falls 10%: gross = qty*(entry-exit) = (160/80000)*8000 = +16 USDT
    res = state.close_position(conn, pid, 72000.0, "signal")
    assert res["return_pct"] > 0
    assert state.account_cash(conn) == pytest.approx(160.0 + res["settlement"])
