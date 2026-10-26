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
import traceback

import pandas as pd

from .. import events
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


SLIPPAGE = 0.0005  # 0.05% adverse slippage on taker fills (pessimistic)


def live_quote(client: WallexPublicClient, symbol: str) -> dict:
    """Best-effort quote: mid from markets snapshot, fallback to last trade."""
    try:
        symbols = client.markets()
        st = symbols.get(symbol, {}).get("stats", {})
        bid = float(st.get("bidPrice") or 0)
        ask = float(st.get("askPrice") or 0)
        last = float(st.get("lastPrice") or 0)
        if bid and ask:
            return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2, "last": last or (bid + ask) / 2}
        if last:
            return {"bid": last, "ask": last, "mid": last, "last": last}
    except Exception:
        pass
    trades = client.trades(symbol)
    px = float(trades[0]["price"]) if trades else 0.0
    return {"bid": px, "ask": px, "mid": px, "last": px}


def entry_fill(q: dict) -> float:
    """We are takers buying: pay the ask plus slippage."""
    return q["ask"] * (1 + SLIPPAGE) if q["ask"] else q["mid"]


def exit_fill(q: dict) -> float:
    """Closing a long sells into the bid minus slippage."""
    return q["bid"] * (1 - SLIPPAGE) if q["bid"] else q["mid"]


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


def run_cycle(notify=None, force: bool = False) -> dict:
    client = WallexPublicClient()
    conn = state.connect()
    state.init_account(cash=200.0)
    if state.get_setting(conn, "halted") == "1":
        events.record("warn", "cycle",
                      "⛔ چرخه اجرا نشد: حساب در حالت HALTED است (داشبورد: ▶ ادامه)")
        equity = state.snapshot_equity(conn, {})
        conn.close()
        return {"ts": int(time.time()), "equity": equity, "open": 0, "closed": 0,
                "positions": [],
                "lines": ["⛔ HALTED — no actions (resume from dashboard)"]}

    if not force:
        last = state.last_cycle_day(conn)
        if last and (int(time.time()) - last) < 20 * 3600:
            events.record("info", "cycle",
                          "چرخهٔ امروز قبلاً اجرا شده (برای اجرای مجدد force لازم است)")
            conn.close()
            return {"ts": int(time.time()), "equity": state.account_cash(conn),
                    "open": 0, "closed": 0, "positions": [],
                    "lines": ["⏭ today's cycle already ran (use force to override)"]}

    events.record("info", "cycle",
                  f"شروع چرخه (force={force}) — به‌روزرسانی کندل‌ها از والکس…")
    try:
        frames = refresh_candles(client)
    except Exception as e:
        events.record("error", "cycle", f"خطا در به‌روزرسانی کندل‌ها: {e}",
                      traceback.format_exc()[-1200:])
        conn.close()
        return {"ts": int(time.time()), "equity": state.account_cash(conn),
                "open": 0, "closed": 0, "positions": [],
                "lines": [f"❌ candle refresh failed: {e}"]}
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
        try:
            quote = live_quote(client, sym)
        except Exception as e:
            events.record("warn", "cycle", f"{sym}: خطا در دریافت قیمت لایو: {e}")
            state.record_decision(conn, sym, "skip", {"why": f"quote error: {e}"})
            continue
        px = quote["mid"]
        prices[sym] = px
        entry_sig = bool(sig["long_entry"].iloc[-1])
        exit_sig = bool(sig["long_exit"].iloc[-1])

        pos = conn.execute("SELECT * FROM positions WHERE symbol=?", (sym,)).fetchone()

        # 0) LLM analyst opinion (cached per day; memory of its past calls)
        opinion = None
        if PAPER_CFG["use_llm"]:
            if state.analyst_today(conn, sym):
                rows = state.recent_analyst(conn, sym, 1)
                if rows:
                    r0 = rows[0]
                    opinion = {"bias": r0["bias"], "confidence": r0["confidence"],
                               "entry_ok": bool(r0["entry_ok"]),
                               "size_factor": r0["size_factor"] or 0.5,
                               "key_levels": {"support": r0["support"],
                                              "resistance": r0["resistance"]},
                               "summary": r0["summary"], "ok": True, "cached": True}
            else:
                memory = []
                now_px = float(df["close"].iloc[-1])
                for r in state.recent_analyst(conn, sym, 3):
                    hist = df[df["ts"] <= r["ts"] + 86400]
                    then_px = float(hist["close"].iloc[-1]) if len(hist) else now_px
                    memory.append({"date": r["ts"], "bias": r["bias"],
                                   "confidence": r["confidence"],
                                   "outcome_pct": round((now_px / then_px - 1) * 100, 1)})
                opinion = analyst.analyze(sym, df, prem_z, dict(pos) if pos else None,
                                          {"entry_sig": entry_sig, "exit_sig": exit_sig},
                                          memory=memory)
                state.save_analyst(conn, sym, opinion)
                if not opinion.get("ok"):
                    events.record("warn", "llm",
                                  f"{sym}: تحلیل AI ناموفق — {opinion.get('summary', '')[:200]}")

        # 1) liquidation watch (deterministic, always)
        if pos is not None:
            if (pos["side"] == "long" and quote["bid"] <= pos["liq_px"]) or \
               (pos["side"] == "short" and quote["ask"] >= pos["liq_px"]):
                res = state.close_position(conn, pos["id"], exit_fill(quote), "liquidated")
                state.record_decision(conn, sym, "liquidate",
                                      {"px": px, **{k: round(v, 4) for k, v in res.items()}})
                closed.append(f"⛔ {sym} LIQUIDATED {res['return_pct']:+.1f}%")
                pos = None

        # 2) exits (deterministic; LLM cannot block)
        if pos is not None and exit_sig:
            res = state.close_position(conn, pos["id"], exit_fill(quote), "signal")
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
                        state.open_position(conn, sym, "long", entry_fill(quote), coll,
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
                        state.open_position(conn, sym, "long", entry_fill(quote), coll,
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
    auto_halted = False
    peak = state.peak_equity(conn)
    if peak > 0 and equity < peak * 0.90:
        state.set_setting(conn, "halted", "1")
        auto_halted = True
        summary_lines_extra = [f"⛔ AUTO-HALT: equity {equity:.2f} is >10% below peak {peak:.2f}"]
        events.record("warn", "risk", summary_lines_extra[0])
    else:
        summary_lines_extra = []
    summary = {"ts": int(time.time()), "equity": round(equity, 2),
               "open": len(opened), "closed": len(closed),
               "positions": [r["symbol"] for r in
                             conn.execute("SELECT symbol FROM positions")],
               "lines": opened + closed +
                        [f"holding: {', '.join(held) or '-'}",
                         f"premium_z: {prem_z:+.2f}" if prem_z is not None else "premium_z: n/a",
                         f"equity: {equity:.2f} USDT"]}
    summary["lines"] = summary["lines"] + summary_lines_extra
    summary["auto_halted"] = auto_halted
    events.record("info", "cycle",
                  "چرخه تمام شد — " + " · ".join((opened + closed) or ["بدون معاملهٔ جدید"])
                  + f" · equity {equity:.2f} USDT")
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
