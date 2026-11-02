"""MarketMind dashboard v3 — multi-page, live, black-based vivid dark theme.

Pages (hash-routed SPA):
  #/overview   KPIs, equity, positions, AI analyst, decisions, controls
  #/markets    live table: price, 24h change, volume, regime state, AI bias
  #/coin/SYM   candlestick chart (1h/4h/1D), SMA overlay, volume, indicators,
               analyst view, this coin's paper trades
  #/research   experiment ledger rendered
  #/settings   secrets status, controls, notify test
Live prices: single /v1/markets call, 15s server cache, 15s client poll.
No external CDNs — everything vanilla.
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import accounts, events
from .config import CANDLES_DIR, DATA_DIR, REPORTS_DIR, RESOLUTIONS
from .paper import state

app = FastAPI(title="MarketMind", docs_url=None, redoc_url=None)
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")

LOGIN_FILE = DATA_DIR / "secrets" / "dashboard_login.json"
ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
          "ADAUSDT", "LINKUSDT", "DOTUSDT", "DOGEUSDT", "USDTTMN"]
_cache: dict = {"markets": None, "markets_ts": 0.0, "signals": {}, "signals_ts": 0.0}
_run_lock = threading.Lock()
_login_lock = threading.Lock()
_login_attempts: dict[str, list[float]] = {}


@app.on_event("startup")
def _boot() -> None:
    """Server-mode boot: seed data on first deploy + run the daily cycle in-process.

    Enabled by MARKETMIND_SCHEDULER=1 (set in the deployment image). Local runs
    of `paper serve` keep using the external daily automation instead.
    """
    import os

    from .data.ingest import seed_if_empty

    seed_if_empty()
    accounts.migrate_legacy_owner()
    accounts._key()
    if os.environ.get("MARKETMIND_SCHEDULER") != "1":
        return

    def cycle_loop() -> None:
        """Continuous paper-trading loop — master switch = settings.auto_trade.

        ON  → full cycle (candle refresh + decisions + paper orders) every
              PAPER_CFG['cycle_interval_min'] minutes, until switched off.
        OFF → no automatic cycles (the manual ▶ button still works)."""
        import time as _t

        from .paper import runner

        while True:
            for user in accounts.users() or [{"account_id": "owner"}]:
                with state.use_account(user["account_id"]):
                    try:
                        conn = state.connect()
                        auto = state.get_setting(conn, "auto_trade", "0") == "1"
                        last = int(state.get_setting(conn, "last_auto_cycle", "0") or 0)
                        conn.close()
                        interval = int(runner.PAPER_CFG.get("cycle_interval_min", 15) * 60)
                        if auto and int(_t.time()) - last >= interval and _run_lock.acquire(blocking=False):
                            try:
                                from . import notify
                                res = runner.run_cycle(notify=notify.send if user["account_id"] == "owner" else None,
                                                       force=True)
                                conn = state.connect()
                                state.set_setting(conn, "last_auto_cycle", str(int(_t.time())))
                                conn.close()
                                _cache["signals"].clear()
                                lines = " · ".join((res.get("lines") or [])[:4])
                                events.record("info", "scheduler", f"چرخهٔ پیوسته تمام شد: {lines[:300]}")
                            finally:
                                _run_lock.release()
                    except Exception as e:  # scheduler must never die
                        events.record("error", "scheduler", f"چرخهٔ پیوسته شکست خورد: {e}",
                                      traceback.format_exc()[-1200:])
            _t.sleep(30)

    threading.Thread(target=cycle_loop, daemon=True, name="mm-scheduler").start()


@app.middleware("http")
async def auth(request: Request, call_next):
    path = request.url.path
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "invalid origin"}, status_code=403)
    if path in ("/", "/login", "/healthz", "/api/login", "/api/public-market") or path.startswith("/static/"):
        return await call_next(request)
    user = accounts.session_user(request.cookies.get("marketmind_session"))
    if not user:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    request.state.user = user
    with state.use_account(user["account_id"]):
        return await call_next(request)


@app.post("/api/login")
async def api_login(request: Request):
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _login_lock:
        attempts = [stamp for stamp in _login_attempts.get(client, []) if now - stamp < 60]
        if len(attempts) >= 10:
            return JSONResponse({"detail": "تعداد تلاش‌ها زیاد است؛ یک دقیقه صبر کن."}, status_code=429)
        attempts.append(now)
        _login_attempts[client] = attempts
    try:
        data = await request.json()
        username, password = str(data.get("username", "")), str(data.get("password", ""))
        if len(username) > 64 or len(password) > 1024:
            raise ValueError("invalid credentials")
        user = accounts.authenticate(username, password)
    except (ValueError, TypeError, AttributeError):
        user = None
    if not user:
        return JSONResponse({"detail": "نام کاربری یا رمز عبور درست نیست."}, status_code=401)
    with _login_lock:
        _login_attempts.pop(client, None)
    response = JSONResponse({"ok": True})
    response.set_cookie("marketmind_session", accounts.make_session(user), max_age=accounts.SESSION_SECONDS,
                        httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return response


@app.post("/api/logout")
def api_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("marketmind_session")
    return response


@app.get("/api/session")
def api_session(request: Request):
    return JSONResponse({"username": request.state.user["username"]})


@app.get("/healthz")
def healthz():
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------- live data
def _markets() -> dict:
    if _cache["markets"] and time.time() - _cache["markets_ts"] < 15:
        return _cache["markets"]
    from .wallex.client import WallexPublicClient
    client = WallexPublicClient(min_interval=0.5, timeout=4, max_retries=1, backoff_base=0.1)
    out = {}
    try:
        symbols = client.markets()
        for sym in ASSETS:
            m = symbols.get(sym)
            if not m:
                continue
            st = m.get("stats", {})
            out[sym] = {
                "last": float(st.get("lastPrice") or 0),
                "ch24": float(st.get("24h_ch") or 0),
                "vol24_quote": float(st.get("24h_quoteVolume") or 0),
                "bid": float(st.get("bidPrice") or 0),
                "ask": float(st.get("askPrice") or 0),
                "high24": float(st.get("24h_highPrice") or 0) or None,
                "low24": float(st.get("24h_lowPrice") or 0) or None,
            }
    except Exception:
        return _cache["markets"] or {"ts": 0, "markets": {}, "error": "market data unavailable"}
    finally:
        client.close()
    data = {"ts": int(time.time()), "markets": out}
    _cache.update({"markets": data, "markets_ts": time.time()})
    return data


@app.get("/api/markets")
def api_markets():
    return JSONResponse(_markets())


def _signals() -> dict:
    """Indicator snapshot per symbol from stored candles (10-min cache)."""
    account_id = state.current_account()
    cached = _cache["signals"].get(account_id)
    if cached and time.time() - cached["ts"] < 600:
        return cached
    import pandas as pd

    from .backtest.strategies import efficiency_ratio

    out = {}
    for sym in ASSETS:
        if sym == "USDTTMN":
            path = CANDLES_DIR / sym / "1D.parquet"
            if not path.exists():
                continue
            df = pd.read_parquet(path).tail(120)
            ret = df["close"].pct_change()
            z = (ret - ret.rolling(60).mean()) / ret.rolling(60).std()
            out[sym] = {"prem_z": round(float(z.iloc[-1]), 2)}
            continue
        path = CANDLES_DIR / sym / "1D.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path).tail(400)
        close = df["close"]
        sma200 = close.rolling(200).mean().iloc[-1]
        sma50 = close.rolling(50).mean().iloc[-1]
        er = efficiency_ratio(close, 20).iloc[-1]
        atr = (df["high"] - df["low"]).rolling(14).mean().iloc[-1]
        out[sym] = {
            "price": float(close.iloc[-1]),
            "sma200": round(float(sma200), 6) if sma200 == sma200 else None,
            "sma50": round(float(sma50), 6) if sma50 == sma50 else None,
            "above_sma200": bool(close.iloc[-1] > sma200) if sma200 == sma200 else None,
            "er": round(float(er), 3) if er == er else None,
            "atr_pct": round(float(atr / close.iloc[-1] * 100), 2) if atr == atr else None,
            "ret20_pct": round(float(close.iloc[-1] / close.iloc[-21] - 1) * 100, 1) if len(close) > 21 else None,
        }
    conn = state.connect()
    for row in conn.execute("SELECT symbol, bias, confidence, entry_ok, summary,"
                            " support, resistance FROM analyst WHERE id IN"
                            " (SELECT MAX(id) FROM analyst GROUP BY symbol)"):
        out.setdefault(row["symbol"], {})
        out[row["symbol"]].update({"ai_bias": row["bias"], "ai_conf": row["confidence"],
                                   "ai_entry": bool(row["entry_ok"]),
                                   "ai_summary": row["summary"],
                                   "ai_support": row["support"], "ai_res": row["resistance"]})
    conn.close()
    data = {"ts": int(time.time()), "signals": out}
    _cache["signals"][account_id] = data
    _cache["signals_ts"] = time.time()
    return data


@app.get("/api/signals")
def api_signals():
    return JSONResponse(_signals())


@app.get("/api/coin/{symbol}")
def api_coin(symbol: str, res: str = "1D", bars: int = 180):
    import pandas as pd

    from .backtest.strategies import efficiency_ratio

    if symbol not in ASSETS or res not in RESOLUTIONS:
        raise HTTPException(404, "unknown symbol/resolution")
    if not 10 <= bars <= 500:
        raise HTTPException(400, "bars must be between 10 and 500")
    path = CANDLES_DIR / symbol / f"{res}.parquet"
    if not path.exists():
        raise HTTPException(404, "no stored candles for this resolution")
    step = RESOLUTIONS[res]
    start = int(time.time()) - (bars + 250) * step  # +250 bars warm-up for SMA200
    try:
        import pyarrow.parquet as pq

        df = pq.read_table(path, filters=[("ts", ">=", start)]).to_pandas()
    except Exception:
        df = pd.read_parquet(path)
        df = df[df["ts"] >= start]
    df = df.sort_values("ts").reset_index(drop=True)
    if df.empty:
        raise HTTPException(404, "no recent candles for this resolution")
    close = df["close"]
    sma200 = close.rolling(200).mean()
    sma50 = close.rolling(50).mean()
    er = efficiency_ratio(close, 20)
    atr = (df["high"] - df["low"]).rolling(14).mean()
    conn = state.connect()
    ana = conn.execute("SELECT * FROM analyst WHERE symbol=? ORDER BY id DESC LIMIT 1",
                       (symbol,)).fetchone()
    ana_hist = [dict(r) for r in conn.execute(
        "SELECT ts, bias, confidence, entry_ok, summary FROM analyst"
        " WHERE symbol=? ORDER BY id DESC LIMIT 6", (symbol,))]
    pos = conn.execute("SELECT * FROM positions WHERE symbol=?", (symbol,)).fetchone()
    trades = [dict(r) for r in conn.execute(
        "SELECT * FROM trades WHERE symbol=? ORDER BY exit_ts DESC LIMIT 20", (symbol,))]
    conn.close()
    tail = df.tail(bars).reset_index(drop=True)
    return JSONResponse({
        "symbol": symbol, "res": res,
        "candles": tail[["ts", "open", "high", "low", "close", "volume"]].to_dict("records"),
        "sma50": [None if v != v else round(float(v), 8) for v in sma50.tail(bars)],
        "sma200": [None if v != v else round(float(v), 8) for v in sma200.tail(bars)],
        "ind": {"er": None if er.iloc[-1] != er.iloc[-1] else round(float(er.iloc[-1]), 3),
                "atr_pct": None if atr.iloc[-1] != atr.iloc[-1] else
                round(float(atr.iloc[-1] / close.iloc[-1] * 100), 2),
                "last": float(close.iloc[-1])},
        "analyst": dict(ana) if ana else None,
        "analyst_hist": ana_hist,
        "position": dict(pos) if pos else None,
        "trades": trades,
        "live": _markets()["markets"].get(symbol),
    })


@app.get("/api/research")
def api_research():
    log = REPORTS_DIR / "research-log.md"
    return JSONResponse({"text": log.read_text() if log.exists() else "دفترچه یافت نشد"})


@app.get("/api/settings")
def api_settings():
    secrets = DATA_DIR / "secrets"
    conn = state.connect()
    halted = state.get_setting(conn, "halted", "0") == "1"
    conn.close()
    return JSONResponse({
        "telegram": (secrets / "telegram.json").exists(),
        "llm": (secrets / "llm_api.json").exists(),
        "owner": state.current_account() == "owner",
        "dashboard_login": accounts.USERS_FILE.exists(),
        "wallex_key": (secrets / "wallex_api_key.txt").exists(),
        "halted": halted,
    })


@app.get("/api/logs")
def api_logs():
    """Latest system events (cycle starts/errors, HALT blocks, LLM failures)."""
    return JSONResponse({"events": events.recent(150)})


@app.get("/api/autotrade")
def api_autotrade_status():
    from .paper import runner

    conn = state.connect()
    on = state.get_setting(conn, "auto_trade", "0") == "1"
    last = int(state.get_setting(conn, "last_auto_cycle", "0") or 0)
    conn.close()
    return JSONResponse({"on": on,
                         "scheduler_enabled": os.environ.get("MARKETMIND_SCHEDULER") == "1",
                         "interval_min": runner.PAPER_CFG.get("cycle_interval_min", 15),
                         "last_cycle_ts": last,
                         "er_min": runner.PAPER_CFG["er_min"],
                         "llm_confidence_min": runner.PAPER_CFG["llm_confidence_min"],
                         "max_positions": runner.PAPER_CFG["max_positions"],
                         "leverage": runner.PAPER_CFG["leverage"],
                         "fraction_of_cash": runner.PAPER_CFG["fraction_of_cash"],
                         "vol_target_risk": runner.PAPER_CFG["vol_target_risk"],
                         "signal_mode": runner.PAPER_CFG.get("signal_mode", "closed"),
                         "max_same_side": runner.PAPER_CFG.get("max_same_side", 3),
                         "btc_filter_shorts": runner.PAPER_CFG.get("btc_filter_shorts", False),
                         "min_size_factor": runner.PAPER_CFG["min_size_factor"]})


@app.post("/api/autotrade")
async def api_autotrade_toggle(request: Request):
    from .paper import runner

    body = await request.json()
    on = bool(body.get("on"))
    conn = state.connect()
    state.set_setting(conn, "auto_trade", "1" if on else "0")
    conn.close()
    if on:
        events.record("info", "scheduler",
                      f"معامله‌گری پیوسته روشن شد — هر {runner.PAPER_CFG.get('cycle_interval_min', 15):.0f} دقیقه بررسی و معامله")
    else:
        events.record("warn", "scheduler", "معامله‌گری پیوسته خاموش شد (دستی توسط کاربر)")
    return JSONResponse({"on": on})


@app.post("/api/run")
def api_run():
    from . import notify
    from .paper import runner
    conn = state.connect()
    halted = state.get_setting(conn, "halted", "0") == "1"
    conn.close()
    if halted:
        events.record("warn", "cycle", "درخواست اجرای چرخه رد شد: حساب HALTED است")
        return JSONResponse({"started": False,
                             "why": "⛔ حساب متوقف (HALT) است — اول دکمهٔ «▶ ادامه» را بزن"})
    if not _run_lock.acquire(blocking=False):
        return JSONResponse({"started": False, "why": "یک چرخه همین حالا در حال اجراست"})

    account_id = state.current_account()

    def go():
        with state.use_account(account_id):
            try:
                res = runner.run_cycle(notify=notify.send if account_id == "owner" else None, force=True)
                _cache["signals"].clear()
                events.record("info", "cycle", "چرخهٔ دستی تمام شد: "
                              + " · ".join((res.get("lines") or [])[:8])[:800])
            except Exception as e:
                events.record("error", "cycle", f"چرخهٔ دستی شکست خورد: {e}",
                              traceback.format_exc()[-1500:])
            finally:
                _run_lock.release()

    threading.Thread(target=go, daemon=True).start()
    return JSONResponse({"started": True})


@app.post("/api/halt")
def api_halt():
    conn = state.connect()
    state.set_setting(conn, "halted", "1")
    conn.close()
    return JSONResponse({"halted": True})


@app.post("/api/resume")
def api_resume():
    conn = state.connect()
    state.set_setting(conn, "halted", "0")
    conn.close()
    return JSONResponse({"halted": False})


@app.post("/api/notify-test")
def api_notify_test():
    if state.current_account() != "owner":
        raise HTTPException(403, "owner only")
    from . import notify
    ok = notify.send("✅ تست اتصال MarketMind به تلگرام")
    return JSONResponse({"sent": bool(ok)})


@app.get("/api/state")
def api_state():
    conn = state.connect()
    cash = state.account_cash(conn)
    positions = [dict(r) for r in conn.execute("SELECT * FROM positions ORDER BY entry_ts")]
    trades = [dict(r) for r in conn.execute("SELECT * FROM trades ORDER BY exit_ts DESC LIMIT 200")]
    decisions = [dict(r) for r in conn.execute("SELECT * FROM decisions ORDER BY ts DESC LIMIT 120")]
    equity = [dict(r) for r in conn.execute("SELECT * FROM equity ORDER BY ts")]
    analyst_rows = [dict(r) for r in conn.execute(
        "SELECT * FROM analyst WHERE id IN (SELECT MAX(id) FROM analyst GROUP BY symbol)")]
    halted = state.get_setting(conn, "halted", "0") == "1"
    auto_trade = state.get_setting(conn, "auto_trade", "0") == "1"
    initial_cash = float(state.get_setting(conn, "initial_cash", "200") or 200)
    peak = state.peak_equity(conn)
    conn.close()

    def stats(sub):
        if not sub:
            return {"n": 0, "win": 0, "exp": 0, "pf": 0}
        wins = [t for t in sub if t["return_pct"] > 0]
        losses = [t for t in sub if t["return_pct"] <= 0]
        return {"n": len(sub), "win": round(len(wins) / len(sub) * 100),
                "exp": round(sum(t["return_pct"] for t in sub) / len(sub), 2),
                "pf": round(sum(t["return_pct"] for t in wins) /
                            max(1e-9, abs(sum(t["return_pct"] for t in losses))), 2)}

    return JSONResponse({
        "cash": cash, "initial_cash": initial_cash, "positions": positions, "trades": trades, "decisions": decisions,
        "equity": equity, "analyst": analyst_rows, "halted": halted,
        "auto_trade": auto_trade,
        "stats_all": stats(trades),
        "stats_signal": stats([t for t in trades if "llm_conviction" not in (t["meta"] or "")]),
        "stats_llm": stats([t for t in trades if "llm_conviction" in (t["meta"] or "")]),
        "peak": peak,
        "drawdown_pct": round((min([e["value"] for e in equity] or [cash]) / peak - 1) * 100, 1) if peak else 0,
        "now": int(time.time()),
    })



@app.get("/api/public-market")
def public_market(symbol: str = "BTCUSDT"):
    """A small, public sample of the latest stored daily closes for the landing page."""
    if symbol not in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        raise HTTPException(404, "unknown market")
    path = CANDLES_DIR / symbol / "1D.parquet"
    if not path.exists():
        return JSONResponse({"symbol": symbol, "candles": []})
    import pandas as pd
    frame = pd.read_parquet(path, columns=["ts", "close"]).tail(45)
    return JSONResponse({"symbol": symbol, "candles": frame.to_dict("records")})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/login")
def login_page():
    return FileResponse(STATIC / "login.html")


@app.get("/app")
def dashboard():
    return FileResponse(STATIC / "app.html")
