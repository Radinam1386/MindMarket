"""Baseline strategies — the reference every fancier idea must beat.

S01 skeleton: Donchian channel breakout.
- entry: close breaks above the highest high of the previous `entry_n` bars
- exit:   close breaks below the lowest low of the previous `exit_n` bars
Channel windows use .shift(1) so the signal bar is never part of its own
channel (no self-lookahead).
"""
from __future__ import annotations

import pandas as pd


def donchian(df: pd.DataFrame, entry_n: int = 20, exit_n: int = 10) -> dict[str, pd.Series]:
    upper = df["high"].rolling(entry_n).max().shift(1)
    lower = df["low"].rolling(exit_n).min().shift(1)
    long_entry = df["close"] > upper
    long_exit = df["close"] < lower

    short_upper = df["high"].rolling(exit_n).max().shift(1)
    short_lower = df["low"].rolling(entry_n).min().shift(1)
    short_entry = df["close"] < short_lower
    short_exit = df["close"] > short_upper

    return {
        "long_entry": long_entry.fillna(False),
        "long_exit": long_exit.fillna(False),
        "short_entry": short_entry.fillna(False),
        "short_exit": short_exit.fillna(False),
    }
