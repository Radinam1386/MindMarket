"""Historical candle backfill from Wallex UDF endpoint into Parquet.

Server behavior (probed 2026-10-25): a request returns the LAST
min(800, window) bars of [from, to] — a full-span window yields only the
most recent 800 bars. Correct paging is therefore BACKWARD: start at `end`,
walk `to` down to first_received_ts - step until no_data or `start` reached.
Every stored series is deduplicated and strictly time-sorted.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timezone

import pandas as pd

from ..config import CANDLES_DIR, DATA_DIR, RESOLUTIONS, ensure_dirs
from ..wallex.client import Bar, WallexPublicClient

log = logging.getLogger("marketmind.ingest")

CANDLE_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]

# Probed 2026-10-25: majors start ~2020-01 (1D), ~2021-01 (4h), ~2021-03 (1h),
# ~2022-10 (15m); scanning earlier is harmless (no_data ends the walk).
DEFAULT_START = datetime(2019, 1, 1, tzinfo=timezone.utc)

MAX_BARS_PER_RESPONSE = 800


def backfill(
    client: WallexPublicClient,
    symbol: str,
    resolution: str,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> pd.DataFrame:
    step = RESOLUTIONS[resolution]
    start = int(start_ts or DEFAULT_START.timestamp())
    to = int(end_ts or _time.time())
    bars: dict[int, Bar] = {}
    guard = 0
    while to > start:
        guard += 1
        if guard > 5000:
            raise RuntimeError(f"backfill guard tripped for {symbol} {resolution}")
        chunk = client.udf_history(symbol, resolution, start, to)
        if not chunk:
            break  # nothing at or below `to` — history exhausted
        for b in chunk:
            bars[b.ts] = b
        new_to = min(b.ts for b in chunk) - step
        if new_to >= to:
            break  # no forward progress — safety
        to = new_to
    df = pd.DataFrame(
        [(ts, b.open, b.high, b.low, b.close, b.volume) for ts, b in sorted(bars.items())],
        columns=CANDLE_COLUMNS,
    )
    log.info("backfill %s %s: %d bars (%s .. %s)", symbol, resolution, len(df),
             _iso(df["ts"].iloc[0]) if len(df) else "-", _iso(df["ts"].iloc[-1]) if len(df) else "-")
    return df


def seed_if_empty() -> bool:
    """On a fresh deployment (empty data dir), copy the baked-in seed data."""
    import shutil

    from ..config import SEED_DIR

    if (CANDLES_DIR / "BTCUSDT" / "1D.parquet").exists() or not SEED_DIR.exists():
        return False
    shutil.copytree(SEED_DIR, DATA_DIR, dirs_exist_ok=True)
    log.info("seeded data dir from %s", SEED_DIR)
    return True


def candles_path(symbol: str, resolution: str):
    return CANDLES_DIR / symbol / f"{resolution}.parquet"


def save_candles(df: pd.DataFrame, symbol: str, resolution: str):
    """Merge with any existing series, dedupe on ts, sort, persist atomically."""
    ensure_dirs()
    path = candles_path(symbol, resolution)
    existing = load_candles(symbol, resolution)
    if existing is not None and len(existing):
        df = pd.concat([existing, df], ignore_index=True)
    df = df.drop_duplicates(subset="ts", keep="last").sort_values("ts").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path, df


def load_candles(symbol: str, resolution: str) -> pd.DataFrame | None:
    path = candles_path(symbol, resolution)
    if not path.exists():
        return None
    return pd.read_parquet(path)


def with_datetime(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["dt"] = pd.to_datetime(out["ts"], unit="s", utc=True)
    return out


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
