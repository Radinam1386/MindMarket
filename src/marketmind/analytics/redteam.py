"""E012: Red-Team attacks on the E011 pooled result.

Five independent attempts to break the PASS:
  A. tail dependence  — drop the two biggest winners (XRP, DOGE); does it stay positive?
  B. sub-periods      — split OOS in halves; positive in BOTH?
  C. trade clustering — entries on the same day across assets = correlated bets;
                        effective sample < trade count.
  D. leverage sweep   — same config at 2x / 3x / 5x.
  E. liquidation audit— forced closes by reason, IS vs OOS.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..backtest.margin import MarginConfig, SizingConfig, run_margin
from ..backtest.strategies import donchian_er
from ..backtest.validation import IS_END, trade_stats
from ..config import (
    MARGIN_LIQ_FRAC,
    MARGIN_LIQ_OFFSET,
    MARGIN_LOAN_FEE_RATE,
    MARGIN_MAX_MIN_NOTIONAL,
    MARGIN_TRADE_FEE_RATE,
)

ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
          "ADAUSDT", "LINKUSDT", "DOTUSDT", "DOGEUSDT"]


def run_asset(df, leverage: float = 3.0):
    cfg = MarginConfig(leverage=leverage, trade_fee_rate=MARGIN_TRADE_FEE_RATE,
                       loan_fee_rate=MARGIN_LOAN_FEE_RATE, liq_frac=MARGIN_LIQ_FRAC,
                       liq_offset=MARGIN_LIQ_OFFSET)
    sz = SizingConfig(fraction_of_cash=0.20, vol_target_risk=0.02,
                      max_notional=MARGIN_MAX_MIN_NOTIONAL[0],
                      min_notional=MARGIN_MAX_MIN_NOTIONAL[1])
    sig = donchian_er(df, entry_n=20, exit_n=10, er_n=20, er_min=0.30)
    return run_margin(df, sig["long_entry"], sig["long_exit"], None, None,
                      cfg=cfg, initial_cash=2000.0, sizing=sz)


def attack_report(frames: dict[str, pd.DataFrame]) -> dict[str, object]:
    is_end_ts = int(pd.Timestamp(IS_END, tz="UTC").timestamp())
    results = {sym: run_asset(df) for sym, df in frames.items()}
    trades = []
    for sym, r in results.items():
        for t in r.trades:
            trades.append({"sym": sym, "ts": t.entry_ts, "exp": t.return_pct,
                           "reason": t.close_reason, "oos": t.entry_ts >= is_end_ts})
    tr = pd.DataFrame(trades)
    oos = tr[tr.oos]

    # A: drop tail winners
    oos_exp_by_sym = oos.groupby("sym")["exp"].mean().sort_values(ascending=False)
    tail_syms = list(oos_exp_by_sym.head(2).index)
    kept = oos[~oos.sym.isin(tail_syms)]

    # B: halves of OOS
    if len(oos):
        mid = (oos.ts.min() + oos.ts.max()) // 2
        h1, h2 = oos[oos.ts < mid], oos[oos.ts >= mid]
    else:
        h1 = h2 = oos.iloc[0:0]

    # C: clustering
    days = pd.to_datetime(oos.ts, unit="s", utc=True).dt.date
    per_day = days.value_counts()
    # D: leverage sweep done by caller via frames at multiple leverages? keep simple:
    # reuse same trades for 3x; leverage sweep needs re-runs (handled in cli)

    # E: reasons
    reasons = tr.groupby(["oos", "reason"]).size().unstack(fill_value=0)

    return {
        "oos_trades": len(oos),
        "oos_exp": float(oos.exp.mean()) if len(oos) else 0.0,
        "oos_win": float((oos.exp > 0).mean() * 100) if len(oos) else 0.0,
        "by_symbol": oos_exp_by_sym,
        "A_tail_syms": tail_syms,
        "A_kept_n": len(kept),
        "A_kept_exp": float(kept.exp.mean()) if len(kept) else 0.0,
        "A_kept_win": float((kept.exp > 0).mean() * 100) if len(kept) else 0.0,
        "B_half1": trade_stats_list(h1),
        "B_half2": trade_stats_list(h2),
        "C_distinct_days": int(per_day.shape[0]),
        "C_max_trades_one_day": int(per_day.max()) if len(per_day) else 0,
        "C_top_days": per_day.head(5).to_dict() if len(per_day) else {},
        "E_reasons": reasons,
        "_results": results,
    }


def trade_stats_list(sub: pd.DataFrame) -> dict:
    if sub.empty:
        return {"n": 0, "exp": 0.0, "win": 0.0}
    return {"n": len(sub), "exp": round(float(sub.exp.mean()), 2),
            "win": round(float((sub.exp > 0).mean() * 100), 1)}


def leverage_sweep(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    is_end_ts = int(pd.Timestamp(IS_END, tz="UTC").timestamp())
    rows = []
    for lev in (2.0, 3.0, 5.0):
        exps = []
        for sym, df in frames.items():
            r = run_asset(df, leverage=lev)
            oos = [t.return_pct for t in r.trades if t.entry_ts >= is_end_ts]
            exps += oos
        rows.append({"leverage": lev, "oos_n": len(exps),
                     "oos_exp_pct": round(float(np.mean(exps)), 2) if exps else 0.0,
                     "oos_win_pct": round(float(np.mean([e > 0 for e in exps]) * 100), 1) if exps else 0.0})
    return pd.DataFrame(rows)
