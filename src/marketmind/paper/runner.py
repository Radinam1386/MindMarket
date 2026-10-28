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

import json
import os
import time
import traceback

import pandas as pd

from .. import events
from ..backtest.strategies import donchian_er, efficiency_ratio
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
    # --- v2.2 anti-churn & quality gates ---
    "max_spread_pct": 0.15,        # skip entries when spread is too wide
    "atr_pct_min": 0.5,            # dead market: skip
    "atr_pct_max": 8.0,            # chaotic market: skip
    "cooldown_days_after_loss": 3, # don't re-enter a symbol right after a loss
    "max_new_per_cycle": 1,        # at most one NEW position per cycle
    "max_entries_7d": 3,           # rolling weekly entry budget
    "prem_veto_z": 1.5,            # risk-off: no new longs when tether premium spikes
    "take_profit_atr": 2.5,        # lock gains at +2.5 ATR (fixes weak exits)
    "ai_trust_min_accuracy": 0.4,  # conviction entries/vetoes need this hit-rate
    "auto_halt_dd_pct": 8.0,       # tighter than 10% for a 200$ account
}


# Server activity tuning via env (research defaults stay intact without them).
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return default


PAPER_CFG["er_min"] = _env_float("MARKETMIND_ER_MIN", PAPER_CFG["er_min"])
PAPER_CFG["llm_confidence_min"] = _env_float("MARKETMIND_LLM_CONF_MIN",
                                             PAPER_CFG["llm_confidence_min"])
PAPER_CFG["max_positions"] = int(_env_float("MARKETMIND_MAX_POSITIONS",
                                            PAPER_CFG["max_positions"]))
PAPER_CFG["cycle_interval_min"] = max(1.0, _env_float("MARKETMIND_CYCLE_MINUTES", 15.0))
PAPER_CFG["allow_shorts"] = os.environ.get("MARKETMIND_ALLOW_SHORTS", "0") == "1"
PAPER_CFG["min_notional"] = _env_float("MARKETMIND_MIN_NOTIONAL", PAPER_CFG["min_notional"])
PAPER_CFG["fraction_of_cash"] = _env_float("MARKETMIND_FRACTION_OF_CASH",
                                           PAPER_CFG["fraction_of_cash"])
PAPER_CFG["vol_target_risk"] = _env_float("MARKETMIND_VOL_TARGET_RISK",
                                          PAPER_CFG["vol_target_risk"])
PAPER_CFG["leverage"] = _env_float("MARKETMIND_LEVERAGE", PAPER_CFG["leverage"])
PAPER_CFG["min_size_factor"] = _env_float("MARKETMIND_MIN_SIZE_FACTOR", 0.0)
PAPER_CFG["stop_atr"] = _env_float("MARKETMIND_STOP_ATR", 2.0)
PAPER_CFG["trail_trigger_atr"] = _env_float("MARKETMIND_TRAIL_TRIGGER_ATR", 1.0)
PAPER_CFG["signal_mode"] = os.environ.get("MARKETMIND_SIGNAL_MODE", "closed")
PAPER_CFG["max_same_side"] = int(_env_float("MARKETMIND_MAX_SAME_SIDE", 3.0))
PAPER_CFG["btc_filter_shorts"] = os.environ.get("MARKETMIND_BTC_FILTER_SHORTS", "0") == "1"


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


REFRESH_SPANS = {"1D": 400 * 86400, "240": 45 * 86400, "60": 4 * 86400}


def refresh_candles(client: WallexPublicClient) -> dict[str, pd.DataFrame]:
    """Always-fresh candles: 1D (signals) + 4h/1h (charts), incl. USDTTMN (premium)."""
    frames: dict[str, pd.DataFrame] = {}
    for sym in ASSETS + ["USDTTMN"]:
        for res, span in REFRESH_SPANS.items():
            try:
                df = ingest.backfill(client, sym, res,
                                     start_ts=int(time.time()) - span)
                _, merged = ingest.save_candles(df, sym, res)
                if res == "1D" and sym in ASSETS:
                    frames[sym] = merged
            except Exception as e:
                events.record("warn", "data", f"{sym} {res}: به‌روزرسانی کندل ناموفق: {e}")
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


def spread_pct(q: dict) -> float:
    if q.get("bid") and q.get("ask") and q["mid"]:
        return (q["ask"] - q["bid"]) / q["mid"] * 100
    return 0.0


def recent_entries(conn, days: int = 7) -> int:
    since = int(time.time()) - days * 86400
    row = conn.execute("SELECT COUNT(*) c FROM trades WHERE entry_ts >= ?", (since,)).fetchone()
    return int(row["c"])


def cooldown_until(conn, symbol: str, days: int) -> int:
    """timestamp until which re-entry on `symbol` is blocked (after a losing close)."""
    row = conn.execute(
        "SELECT exit_ts, return_pct FROM trades WHERE symbol=? ORDER BY exit_ts DESC LIMIT 1",
        (symbol,)).fetchone()
    if row and row["return_pct"] < 0:
        return int(row["exit_ts"]) + days * 86400
    return 0


def ai_trust(conn, df: pd.DataFrame, min_acc: float, window: int = 10) -> dict:
    """Directional accuracy of the analyst's last opinions vs realized 3-day moves.
    None = not enough samples (trust allowed but conviction off)."""
    rows = [dict(r) for r in conn.execute(
        "SELECT ts, bias, confidence FROM analyst ORDER BY id DESC LIMIT ?", (window,))]
    scored = []
    now_px = float(df["close"].iloc[-1])
    for r in rows:
        hist = df[df["ts"] <= r["ts"] + 3 * 86400]
        if hist.empty:
            continue
        then_px = float(hist["close"].iloc[-1])
        if then_px <= 0:
            continue
        moved = (now_px / then_px - 1) * 100
        said_up = r["bias"] == "bullish"
        went_up = moved > 0
        if r["bias"] != "neutral" and abs(moved) > 0.5:  # only score meaningful calls
            scored.append(1.0 if said_up == went_up else 0.0)
    if len(scored) < 5:
        return {"accuracy": None, "n": len(scored), "trusted": False, "conviction": False}
    acc = sum(scored) / len(scored)
    return {"accuracy": round(acc, 2), "n": len(scored),
            "trusted": acc >= min_acc, "conviction": acc >= max(min_acc, 0.55)}


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
    prices, opened, closed, held, held_gate = {}, [], [], [], []

    # BTC market-leader regime on the decision bar — gates counter-trend shorts.
    btc_bull = None
    btc_df = frames.get("BTCUSDT")
    if btc_df is not None and len(btc_df) >= PAPER_CFG["sma_n"] + 5:
        _bi = -1 if PAPER_CFG.get("signal_mode", "closed") == "live" else -2
        btc_close = float(btc_df["close"].iloc[_bi])
        btc_sma = float(btc_df["close"].rolling(PAPER_CFG["sma_n"]).mean().shift(1).iloc[_bi])
        if btc_sma == btc_sma:
            btc_bull = bool(btc_close > btc_sma)
    btc_regime_fa = "نامشخص" if btc_bull is None else ("گاوی" if btc_bull else "نزولی")
    new_this_cycle = 0
    ai_t = None  # analyst trust computed per-symbol below

    for sym in ASSETS:
        df = frames[sym]
        if len(df) < PAPER_CFG["sma_n"] + 5:
            state.record_decision(conn, sym, "skip", {"why": "insufficient history"})
            continue
        sig = donchian_er(df, entry_n=PAPER_CFG["entry_n"], exit_n=PAPER_CFG["exit_n"],
                          er_n=PAPER_CFG["er_n"], er_min=PAPER_CFG["er_min"],
                          sma_n=PAPER_CFG["sma_n"])
        # Decision bar: "closed" (default) evaluates the last COMPLETED daily bar —
        # identical to the backtest engine and golden rule #4 ("signals decide on
        # bar close; execution at the next open"). "live" evaluates the forming bar.
        sig_i = -1 if PAPER_CFG.get("signal_mode", "closed") == "live" else -2
        close_daily = float(df["close"].iloc[sig_i])
        er_now = efficiency_ratio(df["close"], PAPER_CFG["er_n"]).iloc[sig_i]
        sma200_now = float(df["close"].rolling(PAPER_CFG["sma_n"]).mean().shift(1).iloc[sig_i])
        upper_prev = float(df["high"].rolling(PAPER_CFG["entry_n"]).max().shift(1).iloc[sig_i])
        lower_prev = float(df["low"].rolling(PAPER_CFG["exit_n"]).min().shift(1).iloc[sig_i])
        short_exit_px = float(df["high"].rolling(PAPER_CFG["exit_n"]).max().shift(1).iloc[sig_i])
        gate_detail = {"mode": PAPER_CFG.get("signal_mode", "closed"),
                       "er": (round(float(er_now), 3) if er_now == er_now else None),
                       "er_min": PAPER_CFG["er_min"],
                       "above_sma200": bool(close_daily > sma200_now),
                       "breakout": bool(close_daily > upper_prev),
                       "breakdown": bool(close_daily < lower_prev)}
        try:
            quote = live_quote(client, sym)
        except Exception as e:
            events.record("warn", "cycle", f"{sym}: خطا در دریافت قیمت لایو: {e}")
            state.record_decision(conn, sym, "skip", {"why": f"quote error: {e}"})
            continue
        px = quote["mid"]
        prices[sym] = px
        entry_sig = bool(sig["long_entry"].iloc[sig_i])
        exit_sig = bool(sig["long_exit"].iloc[sig_i])

        pos = conn.execute("SELECT * FROM positions WHERE symbol=?", (sym,)).fetchone()

        # 0) LLM analyst opinion (cached per day; memory of its past calls)
        opinion = None
        if PAPER_CFG["use_llm"]:
            if state.analyst_today(conn, sym):
                rows = state.recent_analyst(conn, sym, 1)
                if rows:
                    r0 = rows[0]
                    # defensive .get: a SELECT/legacy-schema missing key must never
                    # kill the whole cycle (was: KeyError 'size_factor'/'support')
                    opinion = {"bias": r0.get("bias") or "neutral",
                               "confidence": float(r0.get("confidence") or 0.0),
                               "entry_ok": bool(r0.get("entry_ok")),
                               "size_factor": r0.get("size_factor") or 0.5,
                               "key_levels": {"support": r0.get("support") or "",
                                              "resistance": r0.get("resistance") or ""},
                               "summary": r0.get("summary") or "",
                               "ok": True, "cached": True}
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
                                          {"entry_sig": entry_sig, "exit_sig": exit_sig,
                                           "portfolio": {"n_open": int(conn.execute(
                                               "SELECT COUNT(*) c FROM positions").fetchone()["c"]),
                                               "total_coll": round(_deployed_collateral(conn), 2),
                                               "btc_regime": btc_regime_fa}},
                                          memory=memory)
                state.save_analyst(conn, sym, opinion)
                if not opinion.get("ok"):
                    events.record("warn", "llm",
                                  f"{sym}: تحلیل AI ناموفق — {opinion.get('summary', '')[:200]}")
                ai_t = ai_trust(conn, df, PAPER_CFG["ai_trust_min_accuracy"])

        # 1) liquidation watch (deterministic, always)
        if pos is not None:
            if (pos["side"] == "long" and quote["bid"] <= pos["liq_px"]) or \
               (pos["side"] == "short" and quote["ask"] >= pos["liq_px"]):
                res = state.close_position(conn, pos["id"], exit_fill(quote), "liquidated")
                state.record_decision(conn, sym, "liquidate",
                                      {"px": px, **{k: round(v, 4) for k, v in res.items()}})
                closed.append(f"⛔ {sym} LIQUIDATED {res['return_pct']:+.1f}%")
                pos = None

        # 1.5) risk exits: ATR stop-loss + breakeven trail (deterministic, always)
        if pos is not None and PAPER_CFG.get("stop_atr", 0) > 0:
            try:
                m = json.loads(pos["meta"] or "{}")
            except Exception:
                m = {}
            entry_px = float(pos["entry_px"])
            atr_entry = float(m.get("atr") or atr) or atr
            stop_k = float(PAPER_CFG["stop_atr"])
            trail_t = float(PAPER_CFG["trail_trigger_atr"])
            best = float(m.get("best_px") or entry_px)
            if pos["side"] == "long":
                best = max(best, px)
                stop_px = entry_px - stop_k * atr_entry
                if best - entry_px >= trail_t * atr_entry:
                    stop_px = max(stop_px, entry_px)  # lock breakeven
                hit = quote["bid"] <= stop_px
            else:
                best = min(best, px)
                stop_px = entry_px + stop_k * atr_entry
                if entry_px - best >= trail_t * atr_entry:
                    stop_px = min(stop_px, entry_px)  # lock breakeven
                hit = quote["ask"] >= stop_px
            if m.get("best_px") != best:
                m["best_px"] = best
                state.update_position_meta(conn, pos["id"], m)
            if hit:
                locked = ((pos["side"] == "long" and stop_px >= entry_px) or
                          (pos["side"] == "short" and stop_px <= entry_px))
                reason = "breakeven_trail" if locked else "stop_loss"
                res = state.close_position(conn, pos["id"],
                                           exit_fill(quote) if pos["side"] == "long"
                                           else entry_fill(quote), reason)
                state.record_decision(conn, sym, "close",
                                      {"px": px, "signal": reason,
                                       "stop_px": round(stop_px, 6),
                                       **{k: round(v, 4) for k, v in res.items()}})
                closed.append(f"🛑 {sym} closed ({reason}) {res['return_pct']:+.1f}%")
                pos = None

        # 2) exits (deterministic; LLM cannot block)
        if pos is not None and pos["side"] == "long" and exit_sig:
            res = state.close_position(conn, pos["id"], exit_fill(quote), "signal")
            state.record_decision(conn, sym, "close",
                                  {"px": px, "signal": "chan10_exit",
                                   **{k: round(v, 4) for k, v in res.items()}})
            closed.append(f"🔒 {sym} closed {res['return_pct']:+.1f}%")
            pos = None
        elif pos is not None and pos["side"] == "short" and close_daily > short_exit_px:
            res = state.close_position(conn, pos["id"], entry_fill(quote), "signal")
            state.record_decision(conn, sym, "close",
                                  {"px": px, "signal": "short_exit",
                                   **{k: round(v, 4) for k, v in res.items()}})
            closed.append(f"🔒 {sym} closed short {res['return_pct']:+.1f}%")
            pos = None

        # 2.5) take-profit (deterministic; LLM cannot block)
        if pos is not None:
            try:
                pmeta = json.loads(pos["meta"] or "{}")
                tp_px = pmeta.get("tp_px")
            except Exception:
                tp_px = None
            if tp_px and ((pos["side"] == "long" and quote["bid"] >= tp_px) or
                          (pos["side"] == "short" and quote["ask"] <= tp_px)):
                res = state.close_position(conn, pos["id"], exit_fill(quote), "take_profit")
                state.record_decision(conn, sym, "close",
                                      {"px": px, "tp_px": round(tp_px, 6),
                                       **{k: round(v, 4) for k, v in res.items()}})
                closed.append(f"🎯 {sym} TP {res['return_pct']:+.1f}%")
                pos = None

        # 3) entries
        if pos is None:
            n_open = conn.execute("SELECT COUNT(*) c FROM positions").fetchone()["c"]
            cash = state.account_cash(conn)
            atr = atr_of(df)
            total_equity_cash = cash + _deployed_collateral(conn)
            allow_shorts = PAPER_CFG.get("allow_shorts", False)
            short_breakdown = bool(close_daily < lower_prev)
            n_shorts = conn.execute(
                "SELECT COUNT(*) c FROM positions WHERE side='short'").fetchone()["c"]
            btc_ok_for_short = (not PAPER_CFG.get("btc_filter_shorts", False)) or \
                sym == "BTCUSDT" or btc_bull is not True

            # v2.2 quality gates (shared by both entry paths)
            gate = None
            spread = spread_pct(quote)
            atr_pct = (atr / px * 100) if (atr and px) else None
            if spread > PAPER_CFG["max_spread_pct"]:
                gate = f"spread {spread:.2f}% > {PAPER_CFG['max_spread_pct']}%"
            elif atr_pct is None or atr_pct < PAPER_CFG["atr_pct_min"]:
                gate = f"atr_pct {atr_pct} too low (dead market)"
            elif atr_pct > PAPER_CFG["atr_pct_max"]:
                gate = f"atr_pct {atr_pct:.1f}% too high (chaos)"
            elif prem_z is not None and prem_z > PAPER_CFG["prem_veto_z"]:
                gate = f"prem_z {prem_z:+.1f} risk-off (no new longs)"
            elif n_open >= PAPER_CFG["max_positions"]:
                gate = "max_positions"
            elif new_this_cycle >= PAPER_CFG["max_new_per_cycle"]:
                gate = "cycle budget (1 new/cycle)"
            elif recent_entries(conn, 7) >= PAPER_CFG["max_entries_7d"]:
                gate = "weekly budget (3/7d)"
            elif time.time() < cooldown_until(conn, sym, PAPER_CFG["cooldown_days_after_loss"]):
                gate = f"cooldown after loss ({PAPER_CFG['cooldown_days_after_loss']}d)"
            if gate:
                state.record_decision(conn, sym, "skip", {"why": gate})
                held_gate.append(sym)
                continue

            if entry_sig:
                # deterministic signal; LLM may veto or scale size down only
                if opinion is not None and opinion["ok"] and not opinion["entry_ok"] and (ai_t is None or ai_t["trusted"]):
                    state.record_decision(conn, sym, "skip",
                                          {"why": "llm_veto",
                                           "llm": opinion["summary"][:200]})
                else:
                    coll = sizing(total_equity_cash, px, atr)
                    if opinion is not None and opinion["ok"]:
                        coll = coll * max(PAPER_CFG["min_size_factor"],
                                          min(1.0, opinion["size_factor"]))
                    if coll <= 0:
                        state.record_decision(conn, sym, "skip",
                                              {"why": "below min notional"})
                    else:
                        entry_px = entry_fill(quote)
                        tp_px = entry_px * (1 + PAPER_CFG["take_profit_atr"] * atr / entry_px) if atr else None
                        state.open_position(conn, sym, "long", entry_px, coll,
                                            PAPER_CFG["leverage"], MARGIN_LIQ_FRAC,
                                            MARGIN_LIQ_OFFSET,
                                            meta={"strategy": "chan10+sma200",
                                                  "llm_bias": (opinion or {}).get("bias"),
                                                  "llm_conf": (opinion or {}).get("confidence"),
                                                  "atr": atr,
                                                  "tp_px": tp_px})
                        new_this_cycle += 1
                        state.record_decision(conn, sym, "open",
                                              {"px": entry_px, "collateral": round(coll, 2),
                                               "atr": round(atr, 2), "path": "signal",
                                               "tp_px": round(tp_px, 6) if tp_px else None,
                                               "llm": (opinion or {}).get("summary", "")[:200]})
                        opened.append(f"🟢 {sym} open(signal) @{entry_px:.4g} coll {coll:.1f}$")
            elif (opinion is not None and opinion["ok"] and opinion["entry_ok"]
                  and opinion["bias"] == "bullish"
                  and (ai_t is None or ai_t["conviction"])
                  and opinion["confidence"] >= PAPER_CFG["llm_confidence_min"]):
                # LLM-conviction entry: half cap, one slot, tagged for scoring
                has_llm = conn.execute(
                    "SELECT COUNT(*) c FROM positions WHERE meta LIKE '%llm_conviction%'"
                ).fetchone()["c"]
                if has_llm:
                    state.record_decision(conn, sym, "skip", {"why": "llm slot busy"})
                else:
                    coll = sizing(total_equity_cash, px, atr,
                                  cap_frac=PAPER_CFG["llm_fraction_cap"])
                    if coll <= 0:
                        state.record_decision(conn, sym, "skip",
                                              {"why": "below min notional"})
                    else:
                        entry_px = entry_fill(quote)
                        tp_px = entry_px * (1 + PAPER_CFG["take_profit_atr"] * atr / entry_px) if atr else None
                        state.open_position(conn, sym, "long", entry_px, coll,
                                            PAPER_CFG["leverage"], MARGIN_LIQ_FRAC,
                                            MARGIN_LIQ_OFFSET,
                                            meta={"strategy": "llm_conviction",
                                                  "llm_conf": opinion["confidence"],
                                                  "atr": atr, "tp_px": tp_px})
                        new_this_cycle += 1
                        state.record_decision(conn, sym, "open",
                                              {"px": entry_px, "collateral": round(coll, 2),
                                               "path": "llm_conviction", "tp_px": round(tp_px, 6) if tp_px else None,
                                               "llm": opinion["summary"][:200]})
                        opened.append(f"🤖 {sym} open(LLM) @{entry_px:.4g} coll {coll:.1f}$ "
                                      f"conf {opinion['confidence']:.0%}")
            elif allow_shorts and short_breakdown and er_now == er_now and er_now >= PAPER_CFG["er_min"]:
                # deterministic short breakdown; bullish LLM opinion or a bullish
                # BTC market regime vetoes (no counter-trend shorts)
                if not btc_ok_for_short:
                    state.record_decision(conn, sym, "skip",
                                          {"why": "btc_bullish_regime"})
                elif opinion is not None and opinion["ok"] and opinion["bias"] == "bullish":
                    state.record_decision(conn, sym, "skip",
                                          {"why": "llm_veto_short",
                                           "llm": opinion["summary"][:200]})
                elif n_open >= PAPER_CFG["max_positions"]:
                    state.record_decision(conn, sym, "skip", {"why": "max_positions"})
                elif n_shorts >= PAPER_CFG.get("max_same_side", 3):
                    state.record_decision(conn, sym, "skip", {"why": "max_shorts"})
                else:
                    coll = sizing(total_equity_cash, px, atr)
                    if opinion is not None and opinion["ok"]:
                        coll = coll * max(PAPER_CFG["min_size_factor"],
                                          min(1.0, opinion["size_factor"]))
                    if coll <= 0:
                        state.record_decision(conn, sym, "skip",
                                              {"why": "below min notional"})
                    else:
                        state.open_position(conn, sym, "short", exit_fill(quote), coll,
                                            PAPER_CFG["leverage"], MARGIN_LIQ_FRAC,
                                            MARGIN_LIQ_OFFSET,
                                            meta={"strategy": "donchian_short",
                                                  "llm_bias": (opinion or {}).get("bias"),
                                                  "atr": atr})
                        state.record_decision(conn, sym, "open",
                                              {"px": px, "collateral": round(coll, 2),
                                               "atr": round(atr, 2), "path": "short_signal",
                                               "llm": (opinion or {}).get("summary", "")[:200]})
                        opened.append(f"🔻 {sym} open(short) @{px:.4g} coll {coll:.1f}$")
            elif (allow_shorts and opinion is not None and opinion["ok"]
                  and opinion["entry_ok"] and opinion["bias"] == "bearish"
                  and opinion["confidence"] >= PAPER_CFG["llm_confidence_min"]):
                # LLM-conviction short: half cap, one slot, tagged for scoring
                has_llm = conn.execute(
                    "SELECT COUNT(*) c FROM positions WHERE meta LIKE '%llm_conviction%'"
                ).fetchone()["c"]
                if not btc_ok_for_short:
                    state.record_decision(conn, sym, "skip",
                                          {"why": "btc_bullish_regime"})
                elif n_open >= PAPER_CFG["max_positions"]:
                    state.record_decision(conn, sym, "skip", {"why": "max_positions"})
                elif has_llm:
                    state.record_decision(conn, sym, "skip", {"why": "llm slot busy"})
                elif n_shorts >= PAPER_CFG.get("max_same_side", 3):
                    state.record_decision(conn, sym, "skip", {"why": "max_shorts"})
                else:
                    coll = sizing(total_equity_cash, px, atr,
                                  cap_frac=PAPER_CFG["llm_fraction_cap"])
                    if coll <= 0:
                        state.record_decision(conn, sym, "skip",
                                              {"why": "below min notional"})
                    else:
                        state.open_position(conn, sym, "short", exit_fill(quote), coll,
                                            PAPER_CFG["leverage"], MARGIN_LIQ_FRAC,
                                            MARGIN_LIQ_OFFSET,
                                            meta={"strategy": "llm_conviction_short",
                                                  "llm_conf": opinion["confidence"],
                                                  "atr": atr})
                        state.record_decision(conn, sym, "open",
                                              {"px": px, "collateral": round(coll, 2),
                                               "path": "llm_conviction_short",
                                               "llm": opinion["summary"][:200]})
                        opened.append(f"🤖 {sym} open(LLM short) @{px:.4g} coll {coll:.1f}$ "
                                      f"conf {opinion['confidence']:.0%}")
            else:
                state.record_decision(conn, sym, "hold",
                                      {"px": px, "note": "no signal", **gate_detail})
        else:
            held.append(sym)
            dist = ((pos["liq_px"] / px if pos["side"] == "short" else px / pos["liq_px"]) - 1) * 100
            state.record_decision(conn, sym, "hold",
                                  {"px": px, "liq_px": round(pos["liq_px"], 2),
                                   "distance_pct": round(dist, 1),
                                   "llm_bias": (opinion or {}).get("bias")})

    equity = state.snapshot_equity(conn, prices)
    auto_halted = False
    peak = state.peak_equity(conn)
    if peak > 0 and equity < peak * (1 - PAPER_CFG["auto_halt_dd_pct"] / 100):
        state.set_setting(conn, "halted", "1")
        auto_halted = True
        summary_lines_extra = [f"⛔ AUTO-HALT: equity {equity:.2f} is >{PAPER_CFG['auto_halt_dd_pct']:.0f}% below peak {peak:.2f}"]
        events.record("warn", "risk", summary_lines_extra[0])
    else:
        summary_lines_extra = []
    summary = {"ts": int(time.time()), "equity": round(equity, 2),
               "open": len(opened), "closed": len(closed),
               "positions": [r["symbol"] for r in
                             conn.execute("SELECT symbol FROM positions")],
               "lines": opened + closed +
                        [f"gate-skipped: {', '.join(held_gate) or '-'}" if held_gate else None,
                         f"holding: {', '.join(held) or '-'}",
                         f"premium_z: {prem_z:+.2f}" if prem_z is not None else "premium_z: n/a",
                         f"equity: {equity:.2f} USDT"]}
    summary["lines"] = [x for x in summary["lines"] if x] + summary_lines_extra
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
