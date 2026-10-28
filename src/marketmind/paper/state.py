"""Paper-trading account state (SQLite) — the Stage-4 ledger.

Tables: account (single row), positions, trades, decisions, equity.
Fee model = measured Wallex margin structure (config constants).
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import (
    DATA_DIR,
    MARGIN_LOAN_FEE_RATE,
    MARGIN_RENEWAL_INTERVAL_HOURS,
    MARGIN_TRADE_FEE_RATE,
)

DB_PATH = DATA_DIR / "paper.sqlite"
RENEWAL_SECONDS = MARGIN_RENEWAL_INTERVAL_HOURS * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash REAL NOT NULL,           -- total equity while flat (USDT)
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,           -- long
    qty REAL NOT NULL,            -- base units
    entry_px REAL NOT NULL,
    collateral REAL NOT NULL,
    leverage REAL NOT NULL,
    entry_ts INTEGER NOT NULL,
    liq_px REAL NOT NULL,
    accrued_interest REAL NOT NULL DEFAULT 0.0,
    meta TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, side TEXT NOT NULL,
    entry_ts INTEGER NOT NULL, exit_ts INTEGER NOT NULL,
    entry_px REAL NOT NULL, exit_px REAL NOT NULL,
    qty REAL NOT NULL, collateral REAL NOT NULL, leverage REAL NOT NULL,
    interest REAL NOT NULL, fees REAL NOT NULL,
    return_pct REAL NOT NULL, close_reason TEXT NOT NULL,
    meta TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL, symbol TEXT NOT NULL,
    action TEXT NOT NULL,          -- open | close | hold | skip | liquidate
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS equity (
    ts INTEGER PRIMARY KEY, value REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS analyst (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    bias TEXT NOT NULL,            -- bullish | bearish | neutral
    confidence REAL NOT NULL,      -- 0..1
    entry_ok INTEGER NOT NULL,     -- 0/1
    size_factor REAL NOT NULL,     -- 0..1
    support TEXT, resistance TEXT,
    summary TEXT NOT NULL,
    raw TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
"""


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    conn.commit()


def save_analyst(conn: sqlite3.Connection, symbol: str, a: dict) -> None:
    conn.execute(
        "INSERT INTO analyst (ts, symbol, bias, confidence, entry_ok, size_factor,"
        " support, resistance, summary, raw) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (int(time.time()), symbol, a.get("bias", "neutral"),
         float(a.get("confidence", 0.0)), int(bool(a.get("entry_ok", False))),
         float(a.get("size_factor", 0.0)),
         str(a.get("key_levels", {}).get("support", "")),
         str(a.get("key_levels", {}).get("resistance", "")),
         a.get("summary", "")[:500], json.dumps(a, ensure_ascii=False)))
    conn.commit()


def recent_analyst(conn: sqlite3.Connection, symbol: str, limit: int = 3) -> list[dict]:
    # Must select every key run_cycle's cached-opinion branch reads (ts, bias,
    # confidence, entry_ok, size_factor, support, resistance, summary) — a missing
    # key crashed the whole cycle with KeyError (bug found via the dashboard log).
    return [dict(r) for r in conn.execute(
        "SELECT ts, bias, confidence, entry_ok, size_factor, support, resistance,"
        " summary FROM analyst"
        " WHERE symbol=? ORDER BY id DESC LIMIT ?", (symbol, limit))]


def analyst_today(conn: sqlite3.Connection, symbol: str) -> bool:
    day_start = int(time.time()) - (int(time.time()) % 86400)
    row = conn.execute("SELECT 1 FROM analyst WHERE symbol=? AND ts>=? LIMIT 1",
                       (symbol, day_start)).fetchone()
    return row is not None


def peak_equity(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT COALESCE(MAX(value),0) m FROM equity").fetchone()
    return float(row["m"])


def last_cycle_day(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(ts) m FROM decisions").fetchone()
    return int(row["m"]) if row and row["m"] else None


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after early Stage-4 DBs (CREATE IF NOT EXISTS cannot)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(analyst)")}
    if cols and "size_factor" not in cols:
        conn.execute("ALTER TABLE analyst ADD COLUMN size_factor REAL NOT NULL DEFAULT 0.5")
        conn.commit()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def init_account(cash: float = 200.0) -> None:
    conn = connect()
    if conn.execute("SELECT 1 FROM account").fetchone() is None:
        conn.execute("INSERT INTO account (id, cash, created_at) VALUES (1, ?, ?)",
                     (cash, int(time.time())))
        conn.commit()
    conn.close()


def account_cash(conn: sqlite3.Connection) -> float:
    return float(conn.execute("SELECT cash FROM account WHERE id=1").fetchone()["cash"])


def set_cash(conn: sqlite3.Connection, cash: float) -> None:
    conn.execute("UPDATE account SET cash=? WHERE id=1", (cash,))
    conn.commit()


def open_position(conn: sqlite3.Connection, symbol: str, side: str, price: float,
                  collateral: float, leverage: float, liq_frac: float, liq_offset: float,
                  meta: dict | None = None) -> int:
    qty = collateral * (1 + leverage) / price
    liq_move = max(0.0, liq_frac / leverage - liq_offset)
    liq_px = price * (1 - liq_move) if side == "long" else price * (1 + liq_move)
    cur = conn.execute(
        "INSERT INTO positions (symbol, side, qty, entry_px, collateral, leverage,"
        " entry_ts, liq_px, accrued_interest, meta) VALUES (?,?,?,?,?,?,?,?,0,?)",
        (symbol, side, qty, price, collateral, leverage, int(time.time()), liq_px,
         json.dumps(meta or {})))
    set_cash(conn, account_cash(conn) - collateral)  # collateral leaves idle cash
    conn.commit()
    return int(cur.lastrowid)


def update_position_meta(conn: sqlite3.Connection, pos_id: int, meta: dict) -> None:
    conn.execute("UPDATE positions SET meta=? WHERE id=?",
                 (json.dumps(meta, ensure_ascii=False), pos_id))
    conn.commit()


def position_fees_and_settlement(pos: sqlite3.Row, exit_px: float, exit_ts: int,
                                 close_reason: str) -> tuple[float, float, float]:
    """Returns (interest, total_fees, settlement_cash) with the measured fee model."""
    loan = pos["collateral"] * pos["leverage"]
    renewals = max(0, int((exit_ts - pos["entry_ts"]) // RENEWAL_SECONDS))
    interest = MARGIN_LOAN_FEE_RATE * loan * renewals
    fees = 2 * MARGIN_TRADE_FEE_RATE * loan + MARGIN_LOAN_FEE_RATE * loan + interest
    direction = 1 if pos["side"] == "long" else -1
    gross = pos["qty"] * (exit_px - pos["entry_px"]) * direction
    settlement = pos["collateral"] + gross - fees
    if close_reason == "liquidated":
        settlement = max(0.0, settlement)
    return interest, fees, settlement


def close_position(conn: sqlite3.Connection, pos_id: int, exit_px: float,
                   close_reason: str) -> dict:
    pos = conn.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
    if pos is None:
        raise KeyError(pos_id)
    exit_ts = int(time.time())
    interest, fees, settlement = position_fees_and_settlement(pos, exit_px, exit_ts, close_reason)
    ret_pct = (settlement / pos["collateral"] - 1) * 100
    conn.execute(
        "INSERT INTO trades (symbol, side, entry_ts, exit_ts, entry_px, exit_px, qty,"
        " collateral, leverage, interest, fees, return_pct, close_reason, meta)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pos["symbol"], pos["side"], pos["entry_ts"], exit_ts, pos["entry_px"], exit_px,
         pos["qty"], pos["collateral"], pos["leverage"], interest, fees, ret_pct,
         close_reason, pos["meta"]))
    conn.execute("DELETE FROM positions WHERE id=?", (pos_id,))
    set_cash(conn, account_cash(conn) + settlement)
    conn.commit()
    return {"settlement": settlement, "fees": fees, "interest": interest,
            "return_pct": ret_pct, "reason": close_reason}


def record_decision(conn: sqlite3.Connection, symbol: str, action: str, detail: dict) -> None:
    conn.execute("INSERT INTO decisions (ts, symbol, action, detail) VALUES (?,?,?,?)",
                 (int(time.time()), symbol, action, json.dumps(detail, ensure_ascii=False)))
    conn.commit()


def snapshot_equity(conn: sqlite3.Connection, prices: dict[str, float]) -> float:
    """Total equity = idle cash + mark-to-settle value of open positions."""
    cash = account_cash(conn)
    total = cash
    for pos in conn.execute("SELECT * FROM positions").fetchall():
        px = prices.get(pos["symbol"], pos["entry_px"])
        _, _, settlement = position_fees_and_settlement(pos, px, int(time.time()), "mark")
        total += settlement
    ts = int(time.time())
    conn.execute("INSERT OR REPLACE INTO equity (ts, value) VALUES (?,?)", (ts, total))
    conn.commit()
    return total
