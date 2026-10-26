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
