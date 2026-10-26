import numpy as np
import pandas as pd

from marketmind.data import ingest
from marketmind.wallex.client import Bar


class FakeClient:
    """Emulates the real UDF endpoint: returns the LAST `max_bars_per_call`
    bars of the requested window (verified against api.wallex.ir 2026-10-25)."""

    def __init__(self, bars, max_bars_per_call=3):
        self.bars = sorted(bars, key=lambda b: b.ts)
        self.cap = max_bars_per_call
        self.calls = 0

    def udf_history(self, symbol, resolution, from_ts, to_ts):
        self.calls += 1
        window = [b for b in self.bars if from_ts <= b.ts <= to_ts]
        return window[-self.cap:]


def _mk_bars(start_ts=1_000_000, n=20, step=900):
    rng = np.random.default_rng(7)
    px = 100.0
    bars = []
    for i in range(n):
        o = px
        c = o + rng.normal(0, 1)
        bars.append(Bar(ts=start_ts + i * step, open=o, high=max(o, c) + 0.5,
                        low=min(o, c) - 0.5, close=c, volume=10.0))
        px = c
    return bars


def test_backfill_pages_backward_dedupes_and_sorts():
    bars = _mk_bars()
    client = FakeClient(bars, max_bars_per_call=3)
    df = ingest.backfill(client, "TESTUSDT", "15", start_ts=bars[0].ts, end_ts=bars[-1].ts + 900)
    assert client.calls > 3, "should have paged multiple times"
    assert len(df) == len(bars)
    assert df["ts"].is_monotonic_increasing
    assert df["ts"].is_unique


def test_backfill_handles_leading_empty_history():
    bars = _mk_bars(start_ts=2_000_000)
    client = FakeClient(bars, max_bars_per_call=3)
    # start far before any data exists; the backward walk must stop at no_data
    df = ingest.backfill(client, "TESTUSDT", "15", start_ts=1_000_000, end_ts=bars[-1].ts + 900)
    assert len(df) == len(bars)
    assert int(df["ts"].iloc[0]) == bars[0].ts


def test_backfill_respects_800_bar_server_cap():
    bars = _mk_bars(n=2000)
    client = FakeClient(bars, max_bars_per_call=800)
    df = ingest.backfill(client, "TESTUSDT", "15", start_ts=bars[0].ts, end_ts=bars[-1].ts)
    assert client.calls == 3  # 2000 bars / 800 per response
    assert len(df) == 2000


def test_save_and_reload_roundtrip(tmp_path, monkeypatch):
    bars = _mk_bars()
    client = FakeClient(bars, max_bars_per_call=800)
    df = ingest.backfill(client, "TESTUSDT", "15", start_ts=bars[0].ts, end_ts=bars[-1].ts)
    monkeypatch.setattr(ingest, "CANDLES_DIR", tmp_path)
    path, merged = ingest.save_candles(df, "TESTUSDT", "15")
    assert path.exists()
    # second save merges without duplicating
    _, merged2 = ingest.save_candles(df, "TESTUSDT", "15")
    assert len(merged2) == len(df)
    assert merged2["ts"].is_unique
