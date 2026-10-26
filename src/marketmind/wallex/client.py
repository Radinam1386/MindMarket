"""Wallex public REST client.

Polite by design: throttled to ~1 request/second with jittered exponential
backoff on 429/5xx/transport errors. No API key required for these endpoints.
Verified live 2026-10-25; see docs/MARKETMIND-PLAN.md §3.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

import httpx

from ..config import RESOLUTIONS, WALLEX_BASE_URL

log = logging.getLogger("marketmind.wallex")


class WallexError(RuntimeError):
    pass


@dataclass(frozen=True)
class Bar:
    ts: int  # epoch seconds, UTC, bar open time
    open: float
    high: float
    low: float
    close: float
    volume: float


class WallexPublicClient:
    def __init__(
        self,
        base_url: str = WALLEX_BASE_URL,
        timeout: float = 25.0,
        min_interval: float = 1.0,
        max_retries: int = 5,
        backoff_base: float = 1.5,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"User-Agent": "MarketMindAI/0.1 (quant research)"},
        )
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._last_request = 0.0

    # -- plumbing -----------------------------------------------------------
    def _throttle(self) -> None:
        wait = self._last_request + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get(self, path: str, params: dict | None = None) -> dict:
        last_err: str = ""
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = self._http.get(path, params=params)
            except httpx.TransportError as e:
                last_err = f"transport error: {e}"
                self._sleep_backoff(attempt, last_err)
                continue
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = f"HTTP {resp.status_code}"
                self._sleep_backoff(attempt, last_err)
                continue
            raise WallexError(f"{path} -> HTTP {resp.status_code}: {resp.text[:200]}")
        raise WallexError(f"{path} failed after {self.max_retries} attempts ({last_err})")

    def _sleep_backoff(self, attempt: int, why: str) -> None:
        delay = min(60.0, self.backoff_base * (2**attempt)) + random.uniform(0, 0.3)
        log.warning("retry %d/%d after %s; sleeping %.1fs", attempt + 1, self.max_retries, why, delay)
        time.sleep(delay)

    # -- public endpoints ----------------------------------------------------
    def markets(self) -> dict[str, dict]:
        """All spot markets with metadata (precision, limits, stats)."""
        payload = self.get("/v1/markets")
        symbols = payload.get("result", {}).get("symbols")
        if not isinstance(symbols, dict):
            raise WallexError(f"unexpected /v1/markets payload: {str(payload)[:200]}")
        return symbols

    def depth(self, symbol: str) -> dict:
        return self.get("/v1/depth", {"symbol": symbol})["result"]

    def trades(self, symbol: str) -> list[dict]:
        return self.get("/v1/trades", {"symbol": symbol})["result"]["latestTrades"]

    def udf_history(self, symbol: str, resolution: str, from_ts: int, to_ts: int) -> list[Bar]:
        """OHLCV candles in TradingView-UDF format.

        Returns [] when the window has no data. Raises on invalid resolution.
        """
        if resolution not in RESOLUTIONS:
            raise ValueError(f"resolution must be one of {sorted(RESOLUTIONS)}")
        d = self.get(
            "/v1/udf/history",
            {"symbol": symbol, "resolution": resolution, "from": int(from_ts), "to": int(to_ts)},
        )
        status = d.get("s")
        if status == "no_data":
            return []
        if status != "ok":
            raise WallexError(f"udf/history error for {symbol} {resolution}: {d.get('errmsg')}")
        t = d.get("t", [])
        o, h, l, c = d.get("o", []), d.get("h", []), d.get("l", []), d.get("c", [])
        v = d.get("v", [0.0] * len(t))
        if not (len(t) == len(o) == len(h) == len(l) == len(c)):
            raise WallexError(f"udf/history ragged arrays for {symbol} {resolution}")
        return [
            Bar(ts=int(ts), open=float(o[i]), high=float(h[i]), low=float(l[i]), close=float(c[i]), volume=float(v[i]))
            for i, ts in enumerate(t)
        ]

    def close(self) -> None:
        self._http.close()
