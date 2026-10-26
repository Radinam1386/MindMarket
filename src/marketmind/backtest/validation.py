"""Validation: IS/OOS split, yearly stability, parameter sensitivity.

Protocol (plan §12):
- IS = 2019-10 .. 2023-12 (research); OOS = 2024-01 .. now (holdout, touched ONCE).
- Parameter grid runs on IS only; the declared candidate is then evaluated a single
  time on OOS. No re-testing until it passes.
- Yearly stability table comes from slicing ONE continuous equity curve.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .baselines import donchian
from .margin import MarginConfig, SizingConfig, run_margin
from . import strategies

IS_END = "2024-01-01"  # UTC; OOS starts here


def segment_metrics(eq: pd.Series) -> dict:
    if len(eq) < 3:
        return {"return_pct": 0.0, "max_dd_pct": 0.0, "sharpe": 0.0, "calmar": 0.0, "bars": len(eq)}
    rets = eq.pct_change().dropna()
    total = eq.iloc[-1] / eq.iloc[0] - 1
    dd = (eq / eq.cummax() - 1).min()
    # unit-safe annualization (pandas 3 datetime resolution is not ns — asi8 broke it)
    secs = eq.index.to_series().diff().dt.total_seconds().median()
    bars_per_year = 365.25 * 86400 / secs if secs and secs > 0 else 365.25
    if rets.std() == 0:
        sharpe = 0.0
    else:
        sharpe = float(rets.mean() / rets.std() * np.sqrt(bars_per_year))
    years = max(len(eq) / bars_per_year, 1e-9)
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    return {
        "return_pct": round(total * 100, 1),
        "max_dd_pct": round(dd * 100, 1),
        "sharpe": round(sharpe, 2),
        "calmar": round(cagr / abs(dd), 2) if dd < 0 else 0.0,
        "bars": len(eq),
    }


def yearly_stability(result_equity: pd.Series) -> pd.DataFrame:
    rows = []
    for year, seg in result_equity.groupby(result_equity.index.year):
        m = segment_metrics(seg)
        m["year"] = year
        rows.append(m)
    return pd.DataFrame(rows)[["year", "return_pct", "max_dd_pct", "sharpe", "bars"]]


def trades_in_window(trades: list, start_ts: int, end_ts: int) -> list:
    return [t for t in trades if start_ts <= t.entry_ts < end_ts]


def trade_stats(trades: list) -> dict:
    if not trades:
        return {"n_trades": 0, "win_rate_pct": 0.0, "expectancy_pct": 0.0, "avg_hold_days": 0.0}
    pnl = np.array([t.return_pct for t in trades])
    return {
        "n_trades": len(trades),
        "win_rate_pct": round(float((pnl > 0).mean() * 100), 1),
        "expectancy_pct": round(float(pnl.mean()), 2),
        "avg_hold_days": round(float(np.mean([t.hold_days for t in trades])), 1),
    }


def build_signals(strategy: str, df: pd.DataFrame, params: dict) -> dict:
    if strategy == "donchian":
        return donchian(df, entry_n=params.get("entry_n", 20), exit_n=params.get("exit_n", 10))
    if strategy == "donchian_er":
        return strategies.donchian_er(
            df, entry_n=params.get("entry_n", 20), exit_n=params.get("exit_n", 10),
            er_n=params.get("er_n", 20), er_min=params.get("er_min", 0.30),
            sma_n=params.get("sma_n"))
    if strategy == "squeeze":
        return strategies.vol_squeeze(
            df, time_stop_bars=params.get("time_stop_bars", 5),
            squeeze_max_pctile=params.get("squeeze_max_pctile", 0.25))
    if strategy == "er_trail":
        return strategies.donchian_er_trail(
            df, entry_n=params.get("entry_n", 20), er_n=params.get("er_n", 20),
            er_min=params.get("er_min", 0.30), trail_k=params.get("trail_k", 3.0),
            use_channel_exit=params.get("use_channel_exit", False),
            exit_n=params.get("exit_n", 10),
            time_stop_bars=params.get("time_stop_bars"),
            sma_n=params.get("sma_n"))
    if strategy == "donchian_er_prem":
        if "prem_z" not in df.columns:
            raise ValueError("donchian_er_prem needs df['prem_z'] attached (see attach_premium)")
        return strategies.donchian_er_premium(
            df, entry_n=params.get("entry_n", 20), exit_n=params.get("exit_n", 10),
            er_n=params.get("er_n", 20), er_min=params.get("er_min", 0.30),
            prem_veto_z=params.get("prem_veto_z", 2.0))
    raise ValueError(f"unknown strategy {strategy}")


def attach_premium(df: pd.DataFrame, prem_df: pd.DataFrame, win: int = 60) -> pd.DataFrame:
    """Merge decision-time-safe USDTTMN z onto a candle frame by ts."""
    from ..analytics.premium import premium_z

    z = premium_z(prem_df, win)
    out = df.copy()
    out["prem_z"] = out["ts"].map(z)
    return out


def run_validated(
    df: pd.DataFrame,
    entry_n: int = 20,
    exit_n: int = 10,
    cfg: MarginConfig | None = None,
    sizing: SizingConfig | None = None,
    initial_cash: float = 2000.0,
    strategy: str = "donchian",
    params: dict | None = None,
) -> dict:
    """One continuous run; IS/OOS + yearly tables sliced from its equity & trades."""
    cfg = cfg or MarginConfig(leverage=3.0)
    sizing = sizing or SizingConfig(fraction_of_cash=0.15, vol_target_risk=0.02,
                                    max_notional=1700.0, min_notional=100.0)
    params = dict(params or {})
    params.setdefault("entry_n", entry_n)
    params.setdefault("exit_n", exit_n)
    sig = build_signals(strategy, df, params)
    res = run_margin(df, sig["long_entry"], sig["long_exit"], None, None,
                     cfg=cfg, initial_cash=initial_cash, sizing=sizing)

    is_end_ts = int(pd.Timestamp(IS_END, tz="UTC").timestamp())
    start_ts = int(df["ts"].iloc[0])
    end_ts = int(df["ts"].iloc[-1])

    is_eq = res.equity[res.equity.index < pd.Timestamp(IS_END, tz="UTC")]
    oos_eq = res.equity[res.equity.index >= pd.Timestamp(IS_END, tz="UTC")]

    return {
        "config": {"strategy": strategy, **params, "leverage": cfg.leverage, "sizing": sizing},
        "is": {"equity": segment_metrics(is_eq), **trade_stats(trades_in_window(res.trades, start_ts, is_end_ts))},
        "oos": {"equity": segment_metrics(oos_eq), **trade_stats(trades_in_window(res.trades, is_end_ts, end_ts))},
        "yearly": yearly_stability(res.equity),
        "buy_hold_oos_pct": None,  # filled by caller if desired
        "_res": res,
    }


def parameter_sensitivity(
    df: pd.DataFrame,
    cfg,
    sizing,
    initial_cash: float = 2000.0,
    strategy: str = "donchian",
    prem_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Grid on IN-SAMPLE data only; small per-strategy variant sets."""
    is_df = df[df["ts"] < int(pd.Timestamp(IS_END, tz="UTC").timestamp())].reset_index(drop=True)
    if strategy == "donchian_er_prem":
        if prem_df is None:
            raise ValueError("donchian_er_prem sensitivity needs prem_df")
        is_df = attach_premium(is_df, prem_df)
    grids: dict[str, list[dict]] = {
        "donchian": [{"entry_n": e, "exit_n": x} for e in (10, 20, 30, 40) for x in (5, 10, 20)],
        "donchian_er": [{"entry_n": 20, "exit_n": 10, "er_min": er} for er in (0.15, 0.30, 0.45)],
        "squeeze": [{"time_stop_bars": t} for t in (3, 5, 8)],
        "donchian_er_prem": [{"entry_n": 20, "exit_n": 10, "er_min": 0.30, "prem_veto_z": z}
                             for z in (1.0, 2.0, 3.0)],
    }
    rows = []
    for params in grids[strategy]:
        sig = build_signals(strategy, is_df, params)
        r = run_margin(is_df, sig["long_entry"], sig["long_exit"], None, None,
                       cfg=cfg, initial_cash=initial_cash, sizing=sizing)
        st = trade_stats(r.trades)
        label = ",".join(f"{k}={v}" for k, v in params.items())
        rows.append({"variant": label,
                     "is_return_pct": r.metrics["total_return_pct"],
                     "is_max_dd_pct": r.metrics["max_drawdown_pct"],
                     "is_sharpe": r.metrics["sharpe"],
                     **st})
    return pd.DataFrame(rows)
