"""Data-quality audit for stored candle series.

DQ score in [0,1]: 1 - 0.6*missing_frac - 3*duplicate_frac - 5*ohlc_violation_frac
                  - 0.1*zero_volume_frac  (clipped).
A series must reach MIN_DQ before it may feed any experiment.
"""
from __future__ import annotations

import pandas as pd

from ..config import CANDLES_DIR, RESOLUTIONS
from .ingest import candles_path

MIN_DQ = 0.90


def audit(df: pd.DataFrame, resolution: str) -> dict:
    step = RESOLUTIONS[resolution]
    n = len(df)
    if n == 0:
        return {"bars": 0, "dq": 0.0, "missing": 0, "duplicates": 0,
                "ohlc_violations": 0, "zero_volume_pct": 100.0,
                "start": None, "end": None, "max_gap_bars": 0}

    ts = df["ts"].to_numpy()
    duplicates = int(n - len(set(ts)))
    expected = set(range(int(ts[0]), int(ts[-1]) + 1, step))
    missing = len(expected - set(int(x) for x in ts))

    gaps = []
    prev = ts[0]
    for cur in ts[1:]:
        gaps.append(int((cur - prev) // step) - 1)
        prev = cur
    max_gap = int(max(gaps)) if gaps else 0

    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    viol = int(((h < np_max(o, c)) | (l > np_min(o, c)) | (h < l)).sum())
    zero_vol = float((df["volume"].to_numpy(float) <= 0).mean() * 100)

    span = max((ts[-1] - ts[0]) // step + 1, 1)
    miss_frac = missing / span
    dup_frac = duplicates / (n + duplicates) if (n + duplicates) else 0.0
    viol_frac = viol / n
    dq = 1.0 - 0.6 * miss_frac - 3.0 * dup_frac - 5.0 * viol_frac - 0.001 * zero_vol
    dq = max(0.0, min(1.0, dq))

    return {"bars": n, "dq": round(dq, 4), "missing": missing, "duplicates": duplicates,
            "ohlc_violations": viol, "zero_volume_pct": round(zero_vol, 2),
            "start": int(ts[0]), "end": int(ts[-1]), "max_gap_bars": max_gap}


def np_max(a, b):
    import numpy as np
    return np.maximum(a, b)


def np_min(a, b):
    import numpy as np
    return np.minimum(a, b)


def audit_all() -> pd.DataFrame:
    rows = []
    for res_dir in sorted(CANDLES_DIR.glob("*")):
        if not res_dir.is_dir():
            continue
        for pq in sorted(res_dir.glob("*.parquet")):
            resolution = pq.stem
            if resolution not in RESOLUTIONS:
                continue
            df = pd.read_parquet(pq)
            a = audit(df, resolution)
            a.update({"symbol": res_dir.name, "resolution": resolution,
                      "path": str(pq.relative_to(CANDLES_DIR))})
            rows.append(a)
    if not rows:
        return pd.DataFrame()
    cols = ["symbol", "resolution", "bars", "start", "end", "dq", "missing",
            "duplicates", "ohlc_violations", "zero_volume_pct", "max_gap_bars"]
    return pd.DataFrame(rows)[cols].sort_values(["symbol", "resolution"]).reset_index(drop=True)


def render_report(df: pd.DataFrame) -> str:
    if df.empty:
        return "# Data quality report\n\nNo series stored yet.\n"
    view = df.copy()
    for col in ("start", "end"):
        view[col] = view[col].map(lambda v: pd.to_datetime(v, unit="s", utc=True).strftime("%Y-%m-%d") if v else "-")
    lines = [
        "# Data quality report",
        "",
        f"Generated: {pd.Timestamp.now("UTC").strftime('%Y-%m-%d %H:%M UTC')} · threshold MIN_DQ={MIN_DQ}",
        "",
        view.to_markdown(index=False),
        "",
        "Rules: signals/backtests may only consume series with DQ >= MIN_DQ. "
        "Missing bars are gap-filled only inside the backtest engine (never in storage).",
    ]
    return "\n".join(lines)
