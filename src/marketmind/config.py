"""Central configuration: paths, exchange constants, defaults.

All monetary percentages are fractions (0.003 = 0.30%).
"""
from __future__ import annotations

from pathlib import Path

# Repo root = two levels above this file (src/marketmind/config.py -> repo)
ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = ROOT / "data"
CANDLES_DIR = DATA_DIR / "candles"
MARKETS_DIR = DATA_DIR / "markets"
LOGS_DIR = ROOT / "logs"
REPORTS_DIR = ROOT / "docs" / "reports"

WALLEX_BASE_URL = "https://api.wallex.ir"

# Candle resolutions supported by GET /v1/udf/history (verified 2026-10-25).
# 5 / 30 / 1W are rejected by the endpoint.
# QUIRK (verified): resolution "15" actually returns 1-MINUTE bars (spacing 60s,
# ~2.5M bars 2022-01..now for BTCUSDT). Minute data is therefore available from
# 2022-01; resample to true 15m in the feature pipeline when needed.
RESOLUTIONS: dict[str, int] = {
    "15": 15 * 60,   # server returns 1-minute bars for this value
    "60": 60 * 60,
    "240": 4 * 60 * 60,
    "720": 12 * 60 * 60,
    "1D": 24 * 60 * 60,
}

# Fee schedule (wallex.ir/fee, retrieved 2026-10-25) — Toman base market.
# Tier 1 (0-100M Toman 30d volume): maker 0.25% / taker 0.30%.
# We default research to Tier-1 pessimism (taker both sides).
TAKER_FEE = 0.0030
MAKER_FEE = 0.0025

# Margin product parameters — MEASURED LIVE 2026-10-25 via public dry-run
# (POST /margin-trade/v1/public/dry-run, no API key needed). See research-log.md.
MARGIN_TRADE_FEE_RATE = 0.0025     # 0.25% of loan per leg (default tier; verify per account)
MARGIN_LOAN_FEE_RATE = 1.0 / 6000  # activation (once) + renewal (per 4h), fraction of loan
MARGIN_LIQ_FRAC = 0.8546           # liquidation adverse move = LIQ_FRAC/L - LIQ_OFFSET
MARGIN_LIQ_OFFSET = 0.0045
MARGIN_CALL_FRAC = 0.7037          # warning price adverse move (monitoring only)
MARGIN_CALL_OFFSET = 0.0037
MARGIN_MAX_NOTIONAL_USDT = 1700.0  # BTC/ETH per-position notional cap (public loan endpoint)
MARGIN_MIN_NOTIONAL_USDT = 100.0
# (max_notional, min_notional) convenience for SizingConfig(*MARGIN_MAX_MIN_NOTIONAL)
MARGIN_MAX_MIN_NOTIONAL = (MARGIN_MAX_NOTIONAL_USDT, MARGIN_MIN_NOTIONAL_USDT)
MARGIN_MAX_AGE_DAYS = 21           # documented (docs/margin-intro)
MARGIN_RENEWAL_INTERVAL_HOURS = 4  # documented
# Sensitivity multipliers applied to all fee rates in scenario runs.
MARGIN_FEE_STRESS_SCENARIOS = {"half": 0.5, "real": 1.0, "double": 2.0}


def ensure_dirs() -> None:
    for d in (DATA_DIR, CANDLES_DIR, MARKETS_DIR, LOGS_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
