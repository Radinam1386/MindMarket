"""Backtest engine v0 — spot-style single position, cost-aware.

Discipline (plan §11):
- Signals are evaluated on bar CLOSE; execution happens at the NEXT bar's OPEN
  (no lookahead; enforced structurally by consuming signal[i-1] at bar i).
- Every fill pays the configured fee. No maker-fill fantasy: v0 assumes taker.
- Shorts are supported for margin-strategy research and clearly labeled; on
  Wallex spot-only they are unrealistic (margin product required).
- Equity marks include the hypothetical exit fee (conservative marking).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Trade:
    side: str          # "long" | "short"
    entry_ts: int
    exit_ts: int
    entry_px: float
    exit_px: float
    qty: float
    cash_before: float
    cash_after: float
    return_pct: float  # net, on deployed cash
    hold_bars: int
    reason: str        # "signal" | "forced_end"


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity: pd.Series
    metrics: dict = field(default_factory=dict)


def run(
    df: pd.DataFrame,
    long_entry: pd.Series,
    long_exit: pd.Series,
    short_entry: pd.Series | None = None,
    short_exit: pd.Series | None = None,
    fee_open: float = 0.0030,
    fee_close: float = 0.0030,
    initial_cash: float = 1_000_000.0,
) -> BacktestResult:
    n = len(df)
    if n < 3:
        raise ValueError("need at least 3 bars")
    opens = df["open"].to_numpy(float)
    closes = df["close"].to_numpy(float)
    ts = df["ts"].to_numpy(np.int64)
    dt = pd.to_datetime(pd.Series(ts), unit="s", utc=True)

    le = long_entry.to_numpy(bool)
    lx = long_exit.to_numpy(bool)
    se = short_entry.to_numpy(bool) if short_entry is not None else np.zeros(n, bool)
    sx = short_exit.to_numpy(bool) if short_exit is not None else np.zeros(n, bool)

    cash = initial_cash
    pos = 0            # 0 flat, +1 long, -1 short
    qty = 0.0
    entry_px = 0.0
    entry_i = -1
    entry_cash = 0.0
    equity = np.empty(n)
    trades: list[Trade] = []

    def close_position(i: int, px: float, reason: str) -> None:
        nonlocal cash, pos, qty
        if pos == 1:
            cash = qty * px * (1 - fee_close)
        else:  # short: cash never left; settle pnl minus both fees
            cash = entry_cash + qty * (entry_px - px) - qty * entry_px * fee_open - qty * px * fee_close
        trades.append(Trade(
            side="long" if pos == 1 else "short",
            entry_ts=int(ts[entry_i]), exit_ts=int(ts[i]),
            entry_px=entry_px, exit_px=px, qty=qty,
            cash_before=entry_cash, cash_after=cash,
            return_pct=(cash / entry_cash - 1) * 100,
            hold_bars=i - entry_i, reason=reason,
        ))
        pos, qty = 0, 0.0

    for i in range(n):
        # 1) execute action decided at the close of the previous bar, at this open
        if i > 0:
            s = i - 1
            if pos == 0:
                if le[s] and not lx[s]:
                    qty = cash / (opens[i] * (1 + fee_open))
                    pos, entry_px, entry_i, entry_cash = 1, opens[i], i, cash
                elif se[s] and not sx[s]:
                    pos, entry_px, entry_i, entry_cash, qty = -1, opens[i], i, cash, cash / opens[i]
            else:
                exit_now = (pos == 1 and (lx[s] or se[s])) or (pos == -1 and (sx[s] or le[s]))
                if exit_now:
                    close_position(i, opens[i], "signal")
                    # allow same-bar flip? No — one action per bar (conservative).
                    if le[s] and not lx[s]:
                        qty = cash / (opens[i] * (1 + fee_open))
                        pos, entry_px, entry_i, entry_cash = 1, opens[i], i, cash
                    elif se[s] and not sx[s]:
                        pos, entry_px, entry_i, entry_cash, qty = -1, opens[i], i, cash, cash / opens[i]

        # 2) mark equity at close
        if pos == 1:
            equity[i] = qty * closes[i] * (1 - fee_close)
        elif pos == -1:
            equity[i] = entry_cash + qty * (entry_px - closes[i]) - qty * closes[i] * fee_close
        else:
            equity[i] = cash

    if pos != 0:
        close_position(n - 1, closes[n - 1], "forced_end")

    eq = pd.Series(equity, index=dt, name="equity")
    return BacktestResult(trades=trades, equity=eq, metrics=compute_metrics(eq, trades, ts, closes, pos_bars=None, df=df))


def compute_metrics(eq: pd.Series, trades: list[Trade], ts: np.ndarray, closes: np.ndarray,
                    pos_bars, df: pd.DataFrame) -> dict:
    rets = eq.pct_change().fillna(0.0)
    total_ret = eq.iloc[-1] / eq.iloc[0] - 1
    years = max((ts[-1] - ts[0]) / (365.25 * 86400), 1e-9)
    cagr = (1 + total_ret) ** (1 / years) - 1
    dd = (eq / eq.cummax() - 1).min()
    pnl = np.array([t.return_pct for t in trades])
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    in_market = sum(t.hold_bars for t in trades)
    return {
        "total_return_pct": round(total_ret * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(dd * 100, 2),
        "sharpe": round(float(rets.mean() / rets.std() * np.sqrt(len(rets) / years)) if rets.std() > 0 else 0.0, 2),
        "n_trades": len(trades),
        "win_rate_pct": round(len(wins) / len(pnl) * 100, 1) if len(pnl) else 0.0,
        "expectancy_pct": round(float(pnl.mean()), 3) if len(pnl) else 0.0,
        "avg_win_pct": round(float(wins.mean()), 3) if len(wins) else 0.0,
        "avg_loss_pct": round(float(losses.mean()), 3) if len(losses) else 0.0,
        "profit_factor": round(float(wins.sum() / abs(losses.sum())), 2) if len(losses) and losses.sum() != 0 else float("inf") if len(wins) else 0.0,
        "exposure_pct": round(in_market / max(len(eq), 1) * 100, 1),
        "buy_hold_pct": round((closes[-1] / closes[0] - 1) * 100, 2),
    }
