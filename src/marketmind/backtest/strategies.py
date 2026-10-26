"""Strategy variants beyond the naive baseline.

E008 targets E007's diagnosed failure mode: taking breakouts in non-trending
regimes. Two remedies, both rule-based and lookahead-safe (all rolling windows
shift(1) where the signal bar must not see itself):

1. donchian_er  — Donchian breakout gated by Kaufman Efficiency Ratio
                  (trend quality filter; ER near 1 = clean trend, near 0 = chop).
2. vol_squeeze  — Bollinger-band-width percentile squeeze, breakout entry,
                  time-boxed exit (few, selective, large-expected-move trades).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def donchian_er_trail(
    df: pd.DataFrame,
    entry_n: int = 20,
    er_n: int = 20,
    er_min: float = 0.30,
    atr_n: int = 14,
    trail_k: float = 3.0,
    use_channel_exit: bool = False,
    exit_n: int = 10,
    time_stop_bars: int | None = None,
    sma_n: int | None = None,
) -> dict[str, pd.Series]:
    """E013/E014: ER-gated Donchian entry (optional SMA regime gate) with a
    redesigned exit. Exit fires (evaluated at close, executed next open) when
    ANY enabled condition holds: trailing chandelier stop, optional opposite
    channel, optional time stop."""
    upper = df["high"].rolling(entry_n).max().shift(1)
    er = efficiency_ratio(df["close"], er_n)
    gate = (er >= er_min).fillna(False)
    if sma_n is not None:
        gate = gate & (df["close"] > df["close"].rolling(sma_n).mean().shift(1)).fillna(False)
    long_entry = ((df["close"] > upper) & gate).fillna(False)

    atr = (df["high"] - df["low"]).rolling(atr_n).mean().shift(1)
    trail = _trailing_exit(long_entry, df["close"], atr, trail_k)

    exit_cond = trail
    if use_channel_exit:
        lower = df["low"].rolling(exit_n).min().shift(1)
        exit_cond = exit_cond | (df["close"] < lower).fillna(False)
    if time_stop_bars is not None:
        bars_since = _bars_since(long_entry)
        exit_cond = exit_cond | (bars_since >= time_stop_bars)

    long_exit = exit_cond.fillna(False)
    return {"long_entry": long_entry, "long_exit": long_exit,
            "short_entry": pd.Series(False, index=df.index),
            "short_exit": pd.Series(False, index=df.index)}


def _trailing_exit(long_entry: pd.Series, close: pd.Series, atr: pd.Series, k: float) -> pd.Series:
    """Stateful chandelier-style trailing stop, mirroring engine timing:
    position opens the bar after the entry signal; anchor = max close since open."""
    n = len(close)
    closes = close.to_numpy(float)
    atrs = atr.to_numpy(float)
    sig = long_entry.to_numpy(bool)
    out = np.zeros(n, bool)
    in_pos = False
    pending = False
    anchor = np.nan
    for i in range(n):
        if not in_pos and pending:
            in_pos, anchor, pending = True, closes[i], False
        if in_pos:
            anchor = max(anchor, closes[i])
            if np.isfinite(atrs[i]) and closes[i] < anchor - k * atrs[i]:
                out[i] = True
                in_pos = False
        if not in_pos and sig[i] and not out[i]:
            pending = True
    return pd.Series(out, index=close.index)


def donchian_er_premium(
    df: pd.DataFrame,
    entry_n: int = 20,
    exit_n: int = 10,
    er_n: int = 20,
    er_min: float = 0.30,
    prem_veto_z: float = 2.0,
) -> dict[str, pd.Series]:
    """E010: ER-gated Donchian with a premium-stress veto.

    Requires df["prem_z"] (decision-time-safe USDTTMN z, attached upstream).
    Entries are vetoed when prem_z > prem_veto_z (local risk-off, E009's bearish
    bucket); low/negative z is left permissive (it boosts odds, E009, but we do
    not require it — sample would get too thin)."""
    sig = donchian_er(df, entry_n=entry_n, exit_n=exit_n, er_n=er_n, er_min=er_min)
    veto = (df["prem_z"] > prem_veto_z).fillna(False)
    sig["long_entry"] = sig["long_entry"] & ~veto
    sig["short_entry"] = sig["short_entry"] & ~veto
    return sig


def efficiency_ratio(close: pd.Series, n: int = 20) -> pd.Series:
    """Kaufman ER: |net move| / sum(|bar-to-bar moves|) over n bars, shifted 1."""
    net = close.diff(n).abs()
    path = close.diff().abs().rolling(n).sum()
    er = (net / path.replace(0.0, np.nan)).clip(0, 1)
    return er.shift(1)


def donchian_er(
    df: pd.DataFrame,
    entry_n: int = 20,
    exit_n: int = 10,
    er_n: int = 20,
    er_min: float = 0.30,
    sma_n: int | None = None,
) -> dict[str, pd.Series]:
    upper = df["high"].rolling(entry_n).max().shift(1)
    lower = df["low"].rolling(exit_n).min().shift(1)
    er = efficiency_ratio(df["close"], er_n)
    gate = (er >= er_min).fillna(False)
    if sma_n is not None:
        gate = gate & (df["close"] > df["close"].rolling(sma_n).mean().shift(1)).fillna(False)

    long_entry = ((df["close"] > upper) & gate).fillna(False)
    long_exit = (df["close"] < lower).fillna(False)

    short_upper = df["high"].rolling(exit_n).max().shift(1)
    short_lower = df["low"].rolling(entry_n).min().shift(1)
    short_entry = ((df["close"] < short_lower) & gate).fillna(False)
    short_exit = (df["close"] > short_upper).fillna(False)
    return {"long_entry": long_entry, "long_exit": long_exit,
            "short_entry": short_entry, "short_exit": short_exit,
            "gate": gate}


def vol_squeeze(
    df: pd.DataFrame,
    bb_n: int = 20,
    squeeze_pct_window: int = 252,
    squeeze_max_pctile: float = 0.25,
    break_n: int = 20,
    time_stop_bars: int = 5,
    loss_stop_atr: float = 2.0,
    atr_n: int = 14,
) -> dict[str, pd.Series]:
    """Squeeze breakout: enter when BB width is in its lowest quartile of the
    trailing year AND close breaks the highest high of the prior break_n bars.
    Exit: opposite signal, time stop (time_stop_bars), or ATR stop (approximated
    here as an exit condition evaluated on close; the engine has no per-trade
    stops in v2.1, so ATR stop is folded into the exit signal conservatively)."""
    mid = df["close"].rolling(bb_n).mean()
    sd = df["close"].rolling(bb_n).std()
    bbw = (4 * sd) / mid
    pctile = bbw.rolling(squeeze_pct_window).rank(pct=True).shift(1)
    in_squeeze = (pctile <= squeeze_max_pctile).fillna(False)

    upper = df["high"].rolling(break_n).max().shift(1)
    atr = (df["high"] - df["low"]).rolling(atr_n).mean().shift(1)

    long_entry = ((df["close"] > upper) & in_squeeze).fillna(False)

    # time stop: N bars after entry, or close below entry-anchored ATR stop
    bars_since_entry = _bars_since(long_entry)
    entry_close = _anchor_value(long_entry, df["close"])
    atr_stop_px = entry_close - loss_stop_atr * atr
    long_exit = (
        (bars_since_entry >= time_stop_bars)
        | ((bars_since_entry > 0) & (df["close"] < atr_stop_px))
    ).fillna(False)

    short_lower = df["low"].rolling(break_n).min().shift(1)
    short_entry = ((df["close"] < short_lower) & in_squeeze).fillna(False)
    entry_close_s = _anchor_value(short_entry, df["close"])
    atr_stop_px_s = entry_close_s + loss_stop_atr * atr
    bars_since_short = _bars_since(short_entry)
    short_exit = (
        (bars_since_short >= time_stop_bars)
        | ((bars_since_short > 0) & (df["close"] > atr_stop_px_s))
    ).fillna(False)
    return {"long_entry": long_entry, "long_exit": long_exit,
            "short_entry": short_entry, "short_exit": short_exit}


def _bars_since(signal: pd.Series) -> pd.Series:
    """Bars since the last True (0 on the signal bar itself); huge when never."""
    sig = signal.to_numpy(bool)
    out = np.full(len(sig), 10**6, dtype=np.int64)
    last = -1
    for i in range(len(sig)):
        if sig[i]:
            last = i
        if last >= 0:
            out[i] = i - last
    return pd.Series(out, index=signal.index)


def _anchor_value(signal: pd.Series, values: pd.Series) -> pd.Series:
    """Value at the last True bar of `signal` (forward-filled until reset)."""
    anchored = values.where(signal).ffill()
    return anchored
