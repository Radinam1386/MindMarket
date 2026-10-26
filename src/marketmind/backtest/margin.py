"""Margin (معامله تعهدی) simulation layer — engine v2, CALIBRATED.

All parameters measured live 2026-10-25 via the public dry-run endpoint
(POST /margin-trade/v1/public/dry-run) — no API key required:

- loan = collateral x risk_coef;  position notional = collateral x (1 + risk_coef)
- open_fee  = extend_fee = loan / 6000  (activation once; renewal per 4h)
- trade_fee = 0.25% of loan at open (account-tier dependent; close leg assumed same)
- liquidation adverse move = 0.8546/L - 0.0045   (fit exactly at L=2.5,3,5,10;
  identical structure across BTCUSDT/ETHUSDT/BTCTMN, long and short symmetric)
- call (warning) adverse move ~= 0.7037/L - 0.0037
- max position age 21 days;  position notional capped per market (BTC/ETH: 1700 USDT)

Discipline unchanged: signals on close, execution at next open, no lookahead.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

RENEWAL_SECONDS = 4 * 3600


@dataclass
class MarginConfig:
    leverage: float = 3.0
    trade_fee_rate: float = 0.0025     # of loan, per leg (open; close assumed equal)
    loan_fee_rate: float = 1.0 / 6000  # activation (once) + renewal (per 4h), of loan
    max_age_days: float = 21.0
    liq_frac: float = 0.8546           # adverse move to liquidation = liq_frac/L - liq_offset
    liq_offset: float = 0.0045
    fee_stress: float = 1.0            # multiplier on all fee rates (sensitivity runs)

    def liq_adverse_move(self) -> float:
        return max(0.0, self.liq_frac / self.leverage - self.liq_offset)


@dataclass
class SizingConfig:
    """Position sizing: how much collateral each position posts.

    fraction_of_cash: hard cap on collateral as a fraction of current cash.
    vol_target_risk:  if set, additionally caps collateral so that a 1-ATR adverse
                      move costs ~ vol_target_risk of equity.
    max/min_notional: exchange per-position caps (measured: 100..1700 USDT on BTC/ETH).
    """
    fraction_of_cash: float = 1.0
    vol_target_risk: float | None = None
    atr_n: int = 14
    max_notional: float = float("inf")
    min_notional: float = 0.0

    def collateral(self, cash: float, price: float, leverage: float, atr: float | None) -> float:
        c = cash * self.fraction_of_cash
        if self.vol_target_risk is not None and atr and atr > 0:
            # qty = c*(1+L)/price; 1-ATR adverse cost = qty*atr <= risk*cash
            vol_cap = self.vol_target_risk * cash * price / ((1 + leverage) * atr)
            c = min(c, vol_cap)
        c = min(c, self.max_notional / (1 + leverage))
        if c < self.min_notional / (1 + leverage):
            return 0.0  # below exchange minimum -> skip this entry
        return c


@dataclass
class MarginTrade:
    side: str
    entry_ts: int
    exit_ts: int
    entry_px: float
    exit_px: float
    qty: float
    notional: float
    collateral: float
    interest: float
    total_fees: float          # open+close legs + activation + renewals
    return_pct: float          # net return on collateral
    hold_days: float
    hold_bars: int             # for metrics compatibility
    close_reason: str          # signal | forced_age | liquidated | end_of_data


@dataclass
class MarginResult:
    trades: list[MarginTrade]
    equity: pd.Series
    metrics: dict = field(default_factory=dict)


def run_margin(
    df: pd.DataFrame,
    long_entry: pd.Series,
    long_exit: pd.Series,
    short_entry: pd.Series | None = None,
    short_exit: pd.Series | None = None,
    cfg: MarginConfig | None = None,
    initial_cash: float = 1_000_000.0,
    sizing: SizingConfig | None = None,
) -> MarginResult:
    cfg = cfg or MarginConfig()
    sizing = sizing or SizingConfig()
    n = len(df)
    opens = df["open"].to_numpy(float)
    highs = df["high"].to_numpy(float)
    lows = df["low"].to_numpy(float)
    closes = df["close"].to_numpy(float)
    ts = df["ts"].to_numpy(np.int64)
    dt = pd.to_datetime(pd.Series(ts), unit="s", utc=True)

    # ATR computed only from bars strictly before the current one (shift 1)
    prev_close = pd.Series(closes).shift(1)
    tr = pd.concat(
        [pd.Series(highs) - pd.Series(lows),
         (pd.Series(highs) - prev_close).abs(),
         (pd.Series(lows) - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(sizing.atr_n).mean().shift(1).to_numpy()

    le = long_entry.to_numpy(bool)
    lx = long_exit.to_numpy(bool)
    se = short_entry.to_numpy(bool) if short_entry is not None else np.zeros(n, bool)
    sx = short_exit.to_numpy(bool) if short_exit is not None else np.zeros(n, bool)

    cash = initial_cash  # total equity while flat
    idle = 0.0           # cash not deployed as collateral while in position
    pos = 0  # 0 flat, +1 long, -1 short
    qty = 0.0
    collateral = 0.0
    entry_px = 0.0
    entry_ts = 0
    entry_i = -1
    liq_px = 0.0
    equity = np.empty(n)
    trades: list[MarginTrade] = []

    def loan_value(entry_px: float) -> float:
        # loan = collateral x leverage, valued at entry price
        return collateral * cfg.leverage

    def accrued_interest(exit_ts: int) -> float:
        renewals = max(0, int((exit_ts - entry_ts) // RENEWAL_SECONDS))
        return cfg.loan_fee_rate * cfg.fee_stress * loan_value(entry_px) * renewals

    def settle(i: int, px: float, reason: str) -> None:
        nonlocal cash, pos, qty, idle
        loan = loan_value(entry_px)
        interest = accrued_interest(ts[i])
        direction = pos
        gross = qty * (px - entry_px) * direction
        fees = (cfg.trade_fee_rate * cfg.fee_stress * loan) * 2  # open leg + assumed close leg
        fees += cfg.loan_fee_rate * cfg.fee_stress * loan        # activation
        fees += interest
        settlement = collateral + gross - fees
        if reason == "liquidated":
            settlement = max(0.0, settlement)
        cash = idle + settlement
        idle = 0.0
        trades.append(MarginTrade(
            side="long" if pos == 1 else "short",
            entry_ts=int(entry_ts), exit_ts=int(ts[i]),
            entry_px=entry_px, exit_px=px, qty=qty,
            notional=qty * entry_px, collateral=collateral,
            interest=interest, total_fees=fees,
            return_pct=(settlement / collateral - 1) * 100 if collateral else 0.0,
            hold_days=(ts[i] - entry_ts) / 86400,
            hold_bars=i - entry_i, close_reason=reason,
        ))
        pos, qty = 0, 0.0

    for i in range(n):
        # 1) actions from the previous bar's close, executed at this open
        if i > 0:
            s = i - 1
            if pos != 0:
                age_days = (ts[i] - entry_ts) / 86400
                exit_sig = (pos == 1 and (lx[s] or se[s])) or (pos == -1 and (sx[s] or le[s]))
                if age_days >= cfg.max_age_days:
                    settle(i, opens[i], "forced_age")
                elif exit_sig:
                    settle(i, opens[i], "signal")
            if pos == 0:
                want_long = le[s] and not lx[s]
                want_short = se[s] and not sx[s]
                side = 1 if want_long else (-1 if want_short else 0)
                if side != 0:
                    a = atr[i]
                    a = float(a) if a == a else None  # NaN guard for warm-up bars
                    c = sizing.collateral(cash, opens[i], cfg.leverage, a)
                    if c > 0:
                        pos, collateral = side, c
                        idle = cash - c
                        qty = collateral * (1 + cfg.leverage) / opens[i]
                        entry_px, entry_ts, entry_i = opens[i], int(ts[i]), i
                        liq_px = entry_px * (1 - cfg.liq_adverse_move() * side)

        # 2) intrabar liquidation check
        if pos == 1 and lows[i] <= liq_px:
            settle(i, liq_px, "liquidated")
        elif pos == -1 and highs[i] >= liq_px:
            settle(i, liq_px, "liquidated")

        # 3) mark equity at close (idle cash + position mark, floored at 0)
        close_leg_fee = cfg.trade_fee_rate * cfg.fee_stress * loan_value(entry_px)
        if pos == 1:
            equity[i] = idle + max(0.0, collateral + qty * (closes[i] - entry_px)
                                   - accrued_interest(int(ts[i])) - close_leg_fee)
        elif pos == -1:
            equity[i] = idle + max(0.0, collateral + qty * (entry_px - closes[i])
                                   - accrued_interest(int(ts[i])) - close_leg_fee)
        else:
            equity[i] = cash

    if pos != 0:
        settle(n - 1, closes[n - 1], "end_of_data")

    eq = pd.Series(equity, index=dt, name="equity")
    return MarginResult(trades=trades, equity=eq, metrics=margin_metrics(eq, trades, ts, closes, cfg))


def margin_metrics(eq: pd.Series, trades: list[MarginTrade], ts: np.ndarray, closes: np.ndarray, cfg: MarginConfig) -> dict:
    rets = eq.pct_change().fillna(0.0)
    total_ret = eq.iloc[-1] / eq.iloc[0] - 1
    years = max((ts[-1] - ts[0]) / (365.25 * 86400), 1e-9)
    cagr = (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else -1.0
    dd = (eq / eq.cummax() - 1).min()
    pnl = np.array([t.return_pct for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    reasons = pd.Series([t.close_reason for t in trades]).value_counts().to_dict()
    fees = sum(t.total_fees for t in trades)
    interest = sum(t.interest for t in trades)
    return {
        "leverage": cfg.leverage,
        "fee_stress": cfg.fee_stress,
        "total_return_pct": round(total_ret * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(dd * 100, 2),
        "sharpe": round(float(rets.mean() / rets.std() * np.sqrt(len(rets) / years)) if rets.std() > 0 else 0.0, 2),
        "n_trades": len(trades),
        "win_rate_pct": round(len(wins) / len(pnl) * 100, 1) if len(pnl) else 0.0,
        "expectancy_pct": round(float(pnl.mean()), 2) if len(pnl) else 0.0,
        "profit_factor": round(float(wins.sum() / abs(losses.sum())), 2) if len(losses) and losses.sum() != 0 else float("inf") if len(wins) else 0.0,
        "avg_hold_days": round(float(np.mean([t.hold_days for t in trades])), 1) if trades else 0.0,
        "avg_fees_pct_of_collateral": round(float(np.mean([t.total_fees / t.collateral * 100
                                                          for t in trades if t.collateral > 1e-9])), 2) if trades else 0.0,
        "interest_share_of_fees_pct": round(interest / fees * 100, 1) if fees else 0.0,
        "closes": reasons,
        "buy_hold_pct": round((closes[-1] / closes[0] - 1) * 100, 2),
    }
