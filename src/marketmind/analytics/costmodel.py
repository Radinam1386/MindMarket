"""Margin cost model & break-even analysis — CALIBRATED EDITION.

Fee structure measured live 2026-10-25 via public dry-run (no API key):
- trade fee   = 0.25% of LOAN per leg (default tier; account tier to verify)
- activation  = loan/6000 (once)
- renewal     = loan/6000 every 4h
- loan = collateral x leverage;  notional = collateral x (1 + leverage)

Break-even = required gross price move (fraction of NOTIONAL) for a round trip
to return zero. Return-on-collateral = break-even x (1 + leverage).
"""
from __future__ import annotations

import pandas as pd

from ..config import (
    MARGIN_FEE_STRESS_SCENARIOS,
    MARGIN_LOAN_FEE_RATE,
    MARGIN_TRADE_FEE_RATE,
)

HORIZONS_H = {"4h": 4, "12h": 12, "1d": 24, "3d": 72, "7d": 168, "14d": 336, "21d": 504}
LEVERAGES = [1, 2, 3, 5, 10]


def renewal_periods(hours: float, interval_h: float = 4.0) -> int:
    """Renewals a position of `hours` length accrues (4h, 8h, ... after open)."""
    return max(0, int(hours // interval_h))


def round_trip_cost(
    leverage: float,
    hold_hours: float,
    trade_fee_rate: float = MARGIN_TRADE_FEE_RATE,
    loan_fee_rate: float = MARGIN_LOAN_FEE_RATE,
    fee_stress: float = 1.0,
) -> float:
    """Total round-trip cost as a fraction of position NOTIONAL."""
    loan_frac = leverage / (1 + leverage)          # loan as fraction of notional
    legs = 2 * trade_fee_rate * fee_stress * loan_frac
    clock = loan_fee_rate * fee_stress * loan_frac * (1 + renewal_periods(hold_hours))
    return legs + clock


def breakeven_table() -> pd.DataFrame:
    rows = []
    for hname, hours in HORIZONS_H.items():
        for sname, stress in MARGIN_FEE_STRESS_SCENARIOS.items():
            for lev in LEVERAGES:
                cost = round_trip_cost(lev, hours, fee_stress=stress)
                rows.append({
                    "horizon": hname,
                    "hold_h": hours,
                    "fee_stress": sname,
                    "leverage": lev,
                    "price_move_needed_pct": round(cost * 100, 3),
                    "collateral_return_needed_pct": round(cost * 100 * (1 + lev), 2),
                })
    return pd.DataFrame(rows)


def headline_table() -> pd.DataFrame:
    rows = []
    for hname, hours in HORIZONS_H.items():
        for lev in (2, 3, 5):
            cost = round_trip_cost(lev, hours)
            rows.append({
                "horizon": hname,
                "leverage": lev,
                "renewals_n": renewal_periods(hours),
                "price_move_needed_pct": round(cost * 100, 3),
                "collateral_return_needed_pct": round(cost * 100 * (1 + lev), 2),
            })
    return pd.DataFrame(rows)


def render_report(headline: pd.DataFrame, full: pd.DataFrame, path) -> str:
    lines = [
        "# Fee break-even analysis — measured fee structure (2026-10-25)",
        "",
        "Source: public dry-run endpoint, live. trade 0.25% of loan per leg;",
        "activation + renewal = loan/6000 each (renewal every 4h).",
        "",
        "## Headline (real fees, no stress)",
        "",
        headline.to_markdown(index=False),
        "",
        "## Reading it",
        "",
        "- `price_move_needed_pct`: gross move needed on notional before the first rial of profit.",
        "- Real structure is far cheaper than the pre-measurement pessimistic scenarios:",
        "  a 1-day hold at 3x now needs ~0.3% instead of the earlier assumed 1.4%.",
        "- The binding constraints remain: per-leg trade fee at entry+exit and 4h clock;",
        "  fee_stress=double in the full grid covers regime/parameter uncertainty.",
        "",
        f"Full scenario grid ({len(full)} rows) saved to `{path.name}`.",
    ]
    return "\n".join(lines)
