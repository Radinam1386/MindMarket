"""Paper-trading daily cycle (Stage 4) — HYBRID: deterministic rules + LLM analyst.

Decision policy (LLM advises inside a hard envelope):
- Entries from the deterministic signal (chan10+SMA200) can be VETOED by the LLM
  analyst or size-scaled by its size_factor. LLM can never raise leverage or size
  beyond the deterministic caps.
- LLM-conviction entries (no deterministic signal): allowed only when analyst bias is
  bullish with confidence >= 0.7 and entry_ok — capped at half sizing and at most ONE
  concurrent LLM-initiated position, tagged strategy 'llm_conviction' so we can score
  LLM judgement separately against the rule engine.
- Exits and liquidation watch are ALWAYS deterministic. LLM cannot block an exit.
"""
from __future__ import annotations

import time

import pandas as pd

from ..backtest.strategies import donchian_er
from ..config import (
    MARGIN_LIQ_FRAC,
    MARGIN_LIQ_OFFSET,
    MARGIN_MAX_MIN_NOTIONAL,
)
from ..data import ingest
from ..wallex.client import WallexPublicClient
from . import analyst, state

ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
          "ADAUSDT", "LINKUSDT", "DOTUSDT", "DOGEUSDT"]

PAPER_CFG = {
    "leverage": 3.0,
    "fraction_of_cash": 0.20,
    "llm_fraction_cap": 0.10,
    "llm_confidence_min": 0.70,
    "vol_target_risk": 0.02,
    "atr_n": 14,
    "max_notional": MARGIN_MAX_MIN_NOTIONAL[0],
    "min_notional": MARGIN_MAX_MIN_NOTIONAL[1],
    "max_positions": 3,
    "entry_n": 20,
    "exit_n": 10,
    "er_n": 20,
    "er_min": 0.30,
    "sma_n": 200,
    "use_llm": True,
}


def live_price(client: WallexPublicClient, symbol: str) -> float:
    trades = client.trades(symbol)
    if trades:
        return float(trades[0]["price"])
    depth = client.depth(symbol)
    bid = float(depth["bid"][0]["price"]) if depth.get("bid") else 0.0
    ask = float(depth["ask"][0]["price"]) if depth.get("ask") else 0.0
    return (bid + ask) / 2 or 0.0


def refresh_candles(client: WallexPublicClient) -> dict[str, pd.DataFrame]:
    frames = {}
    for sym in ASSETS:
        df = ingest.backfill(client, sym, "1D",
                             start_ts=int(time.time()) - 400 * 86400)
        _, merged = ingest.save_candles(df, sym, "1D")
        frames[sym] = merged
    return frames


def sizing(cash_total: float, price: float, atr: float, cap_frac: float | None = None) -> float:
    """Collateral: fraction cap (default 20%), vol-target 2%/ATR, exchange bounds."""
    lev = PAPER_CFG["leverage"]
    frac = cap_frac or PAPER_CFG["fraction_of_cash"]
    c = cash_total * frac
    if atr and atr > 0:
        vol_cap = PAPER_CFG["vol_target_risk"] * cash_total * price / ((1 + lev) * atr)
        c = min(c, vol_cap)
    c = min(c, PAPER_CFG["max_notional"] / (1 + lev))
    if c < PAPER_CFG["min_notional"] / (1 + lev):
        return 0.0
    return c


def atr_of(df: pd.DataFrame) -> float:
    tr = (df["high"] - df["low"]).rolling(PAPER_CFG["atr_n"]).mean()
    v = tr.iloc[-2] if len(tr) >= 2 else float("nan")  # last CLOSED bar's ATR
    return float(v) if v == v else 0.0


def run_cycle(notify=None) -> dict:
    client = WallexPublicClient()
    conn = state.connect()
    state.init_account(cash=200.0)
    if state.get_setting(conn, "halted") == "1":
        equity = state.snapshot_equity(conn, {})
        conn.close()
        return {"ts": int(time.time()), "equity": equity, "open": 0, "closed": 0,
                "positions": [],
                "lines": ["⛔ HALTED — no actions (resume from dashboard)"]}

    frames = refresh_candles(client)
    prem_df = ingest.load_candles("USDTTMN", "1D")
    prem_z = analyst.premium_z_now(prem_df) if prem_df is not None else None
    prices, opened, closed, held = {}, [], [], []

    for sym in ASSETS:
        df = frames[sym]
        if len(df) < PAPER_CFG["sma_n"] + 5:
            state.record_decision(conn, sym, "skip", {"why": "insufficient history"})
            continue
        sig = donchian_er(df, entry_n=PAPER_CFG["entry_n"], exit_n=PAPER_CFG["exit_n"],
                          er_n=PAPER_CFG["er_n"], er_min=PAPER_CFG["er_min"],
                          sma_n=PAPER_CFG["sma_n"])
        px = live_price(client, sym)
        prices[sym] = px
        entry_sig = bool(sig["long_entry"].iloc[-1])
        exit_sig = bool(sig["long_exit"].iloc[-1])

        pos = conn.execute("SELECT * FROM positions WHERE symbol=?", (sym,)).fetchone()

        # 0) LLM analyst opinion (advisory; stored for scoring)
        opinion = None
        if PAPER_CFG["use_llm"]:
            opinion = analyst.analyze(sym, df, prem_z, dict(pos) if pos else None,
                                      {"entry_sig": entry_sig, "exit_sig": exit_sig})
            state.save_analyst(conn, sym, opinion)

        # 1) liquidation watch (deterministic, always)
        if pos is not None:
            if (pos["side"] == "long" and px <= pos["liq_px"]) or \
               (pos["side"] == "short" and px >= pos["liq_px"]):
                res = state.close_position(conn, pos["id"], px, "liquidated")
                state.record_decision(conn, sym, "liquidate",
                                      {"px": px, **{k: round(v, 4) for k, v in res.items()}})
                closed.append(f"⛔ {sym} LIQUIDATED {res['return_pct']:+.1f}%")
                pos = None

        # 2) exits (deterministic; LLM cannot block)
        if pos is not None and exit_sig:
            res = state.close_position(conn, pos["id"], px, "signal")
            state.record_decision(conn, sym, "close",
                                  {"px": px, "signal": "chan10_exit",
                                   **{k: round(v, 4) for k, v in res.items()}})
            closed.append(f"🔒 {sym} closed {res['return_pct']:+.1f}%")
            pos = None

        # 3) entries
        if pos is None:
            n_open = conn.execute("SELECT COUNT(*) c FROM positions").fetchone()["c"]
            cash = state.account_cash(conn)
            atr = atr_of(df)
            total_equity_cash = cash + _deployed_collateral(conn)

            if entry_sig:
                # deterministic signal; LLM may veto or scale size down only
                if opinion is not None and opinion["ok"] and not opinion["entry_ok"]:
                    state.record_decision(conn, sym, "skip",
                                          {"why": "llm_veto",
                                           "llm": opinion["summary"][:200]})
                elif n_open >= PAPER_CFG["max_positions"]:
                    state.record_decision(conn, sym, "skip", {"why": "max_positions"})
                else:
                    coll = sizing(total_equity_cash, px, atr)
                    if opinion is not None and opinion["ok"]:
                        coll = coll * min(1.0, opinion["size_factor"])
                    if coll <= 0:
                        state.record_decision(conn, sym, "skip",
                                              {"why": "below min notional"})
                    else:
                        state.open_position(conn, sym, "long", px, coll,
                                            PAPER_CFG["leverage"], MARGIN_LIQ_FRAC,
                                            MARGIN_LIQ_OFFSET,
                                            meta={"strategy": "chan10+sma200",
                                                  "llm_bias": (opinion or {}).get("bias"),
                                                  "llm_conf": (opinion or {}).get("confidence"),
                                                  "atr": atr})
                        state.record_decision(conn, sym, "open",
                                              {"px": px, "collateral": round(coll, 2),
                                               "atr": round(atr, 2), "path": "signal",
                                               "llm": (opinion or {}).get("summary", "")[:200]})
                        opened.append(f"🟢 {sym} open(signal) @{px:.4g} coll {coll:.1f}$")
            elif (opinion is not None and opinion["ok"] and opinion["entry_ok"]
                  and opinion["bias"] == "bullish"
                  and opinion["confidence"] >= PAPER_CFG["llm_confidence_min"]):
                # LLM-conviction entry: half cap, one slot, tagged for scoring
                has_llm = conn.execute(
                    "SELECT COUNT(*) c FROM positions WHERE meta LIKE '%llm_conviction%'"
                ).fetchone()["c"]
                if n_open >= PAPER_CFG["max_positions"]:
                    state.record_decision(conn, sym, "skip", {"why": "max_positions"})
                elif has_llm:
                    state.record_decision(conn, sym, "skip", {"why": "llm slot busy"})
                else:
                    coll = sizing(total_equity_cash, px, atr,
                                  cap_frac=PAPER_CFG["llm_fraction_cap"])
                    if coll <= 0:
                        state.record_decision(conn, sym, "skip",
                                              {"why": "below min notional"})
                    else:
                        state.open_position(conn, sym, "long", px, coll,
                                            PAPER_CFG["leverage"], MARGIN_LIQ_FRAC,
                                            MARGIN_LIQ_OFFSET,
                                            meta={"strategy": "llm_conviction",
                                                  "llm_conf": opinion["confidence"],
                                                  "atr": atr})
                        state.record_decision(conn, sym, "open",
                                              {"px": px, "collateral": round(coll, 2),
                                               "path": "llm_conviction",
                                               "llm": opinion["summary"][:200]})
                        opened.append(f"🤖 {sym} open(LLM) @{px:.4g} coll {coll:.1f}$ "
                                      f"conf {opinion['confidence']:.0%}")
            else:
                state.record_decision(conn, sym, "hold", {"px": px, "note": "no signal"})
        else:
            held.append(sym)
            state.record_decision(conn, sym, "hold",
                                  {"px": px, "liq_px": round(pos["liq_px"], 2),
                                   "distance_pct": round((px / pos["liq_px"] - 1) * 100, 1),
                                   "llm_bias": (opinion or {}).get("bias")})

    equity = state.snapshot_equity(conn, prices)
    summary = {"ts": int(time.time()), "equity": round(equity, 2),
               "open": len(opened), "closed": len(closed),
               "positions": [r["symbol"] for r in
                             conn.execute("SELECT symbol FROM positions")],
               "lines": opened + closed +
                        [f"holding: {', '.join(held) or '-'}",
                         f"premium_z: {prem_z:+.2f}" if prem_z is not None else "premium_z: n/a",
                         f"equity: {equity:.2f} USDT"]}
    if notify:
        try:
            notify("\n".join(["🧠 MarketMind paper (hybrid)"] + summary["lines"]))
        except Exception as e:  # notifications must never break trading
            summary["notify_error"] = str(e)
    conn.close()
    return summary


def _deployed_collateral(conn) -> float:
    row = conn.execute("SELECT COALESCE(SUM(collateral),0) s FROM positions").fetchone()
    return float(row["s"])
