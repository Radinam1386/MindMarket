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

import base64
import binascii
import hmac
import json
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .config import CANDLES_DIR, DATA_DIR, REPORTS_DIR, RESOLUTIONS
from .paper import state

app = FastAPI(title="MarketMind", docs_url=None, redoc_url=None)

LOGIN_FILE = DATA_DIR / "secrets" / "dashboard_login.json"
ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
          "ADAUSDT", "LINKUSDT", "DOTUSDT", "DOGEUSDT", "USDTTMN"]
_cache: dict = {"markets": None, "markets_ts": 0.0, "signals": None, "signals_ts": 0.0}
_run_lock = threading.Lock()


def _login() -> tuple[str, str] | None:
    """Dashboard credentials: data/secrets/dashboard_login.json (username/password),
    with DASHBOARD_USER / DASHBOARD_PASSWORD env fallback."""
    if LOGIN_FILE.exists():
        try:
            cfg = json.loads(LOGIN_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        user, password = cfg.get("username"), cfg.get("password")
        if user and password:
            return str(user), str(password)
    import os
    user = os.environ.get("DASHBOARD_USER")
    password = os.environ.get("DASHBOARD_PASSWORD")
    if user and password:
        return user, password
    return None


def _basic_credentials(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("Authorization") or ""
    if not header.startswith("Basic "):
        return None
    try:
        raw = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    user, _, password = raw.partition(":")
    return user, password


@app.on_event("startup")
def _boot() -> None:
    """Server-mode boot: seed data on first deploy + run the daily cycle in-process.

    Enabled by MARKETMIND_SCHEDULER=1 (set in the deployment image). Local runs
    of `paper serve` keep using the external daily automation instead.
    """
    import os

    from .data.ingest import seed_if_empty

    seed_if_empty()
    if os.environ.get("MARKETMIND_SCHEDULER") != "1":
        return

    def cycle_loop() -> None:
        import time as _t

        while True:
            now = _t.time()
            next_run = (int(now) // 86400 + 1) * 86400 + 3600  # next 01:00 UTC = 04:30 Tehran
            _t.sleep(max(5.0, next_run - now))
            try:
                from . import notify
                from .paper import runner

                runner.run_cycle(notify=notify.send)
                _cache["signals_ts"] = 0.0
            except Exception:
                continue  # scheduler must never die

    threading.Thread(target=cycle_loop, daemon=True, name="mm-scheduler").start()


@app.middleware("http")
async def auth(request: Request, call_next):
    if request.url.path == "/healthz":  # platform healthcheck — never blocked
        return await call_next(request)
    host = request.client.host if request.client else ""
    if host in ("127.0.0.1", "::1", "localhost"):
        return await call_next(request)  # local scripts/healthchecks stay open
    creds_cfg = _login()
    if not creds_cfg:
        return JSONResponse({"detail": "login not configured on the server"
                                     " (data/secrets/dashboard_login.json)"},
                            status_code=403)
    supplied = _basic_credentials(request)
    if (supplied is not None
            and hmac.compare_digest(supplied[0], creds_cfg[0])
            and hmac.compare_digest(supplied[1], creds_cfg[1])):
        return await call_next(request)
    return JSONResponse({"detail": "unauthorized"}, status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="MarketMind", charset="UTF-8"'})


@app.get("/healthz")
def healthz():
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------- live data
def _markets() -> dict:
    if _cache["markets"] and time.time() - _cache["markets_ts"] < 15:
        return _cache["markets"]
    from .wallex.client import WallexPublicClient
    client = WallexPublicClient(min_interval=0.5)
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
    if _cache["signals"] and time.time() - _cache["signals_ts"] < 600:
        return _cache["signals"]
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
    _cache.update({"signals": data, "signals_ts": time.time()})
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
        "dashboard_login": LOGIN_FILE.exists(),
        "wallex_key": (secrets / "wallex_api_key.txt").exists(),
        "halted": halted,
    })


@app.post("/api/run")
def api_run():
    from . import notify
    from .paper import runner
    if not _run_lock.acquire(blocking=False):
        return JSONResponse({"started": False, "why": "a cycle is already running"})

    def go():
        try:
            runner.run_cycle(notify=notify.send, force=True)
            _cache["signals_ts"] = 0.0  # refresh analyst/signal cache
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
        "cash": cash, "positions": positions, "trades": trades, "decisions": decisions,
        "equity": equity, "analyst": analyst_rows, "halted": halted,
        "stats_all": stats(trades),
        "stats_signal": stats([t for t in trades if "llm_conviction" not in (t["meta"] or "")]),
        "stats_llm": stats([t for t in trades if "llm_conviction" in (t["meta"] or "")]),
        "peak": peak,
        "drawdown_pct": round((min([e["value"] for e in equity] or [cash]) / peak - 1) * 100, 1) if peak else 0,
        "now": int(time.time()),
    })


PAGE = r"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MarketMind</title><style>
:root{
 --bg:#07080b;--bg2:#0b0d12;--card:#10131a;--card2:#151923;--line:#1f2431;--line2:#2a3143;
 --txt:#f1f3f9;--dim:#9aa4bb;--dim2:#6b7490;
 --amber:#ffb224;--amber2:#ff8a3d;--pos:#2ee6a0;--neg:#ff6565;--ai:#b48bff;--info:#69c3ff;
}
*{box-sizing:border-box}
body{font-family:system-ui,'Vazirmatn',sans-serif;background:
 radial-gradient(1200px 500px at 85% -10%,#1a1440 0%,transparent 60%),
 radial-gradient(900px 400px at -10% 0%,#0f2a24 0%,transparent 55%),var(--bg);
 color:var(--txt);margin:0;padding:0 20px 40px;min-height:100vh}
nav{position:sticky;top:0;z-index:9;display:flex;gap:6px;align-items:center;padding:12px 0;
 background:linear-gradient(180deg,rgba(7,8,11,.95),rgba(7,8,11,.75));backdrop-filter:blur(8px);
 border-bottom:1px solid var(--line);margin-bottom:16px}
.brand{font-weight:800;font-size:16px;margin-left:18px;
 background:linear-gradient(90deg,var(--amber),var(--amber2),var(--ai));
 -webkit-background-clip:text;background-clip:text;color:transparent}
nav a{color:var(--dim);text-decoration:none;font-size:13px;padding:7px 14px;border-radius:10px;transition:.2s}
nav a:hover{color:var(--txt);background:var(--card2)}
nav a.on{color:#081018;background:linear-gradient(90deg,var(--amber),var(--amber2));font-weight:700}
h1{font-size:19px;margin:0 0 4px} h3{font-size:13.5px;margin:0 0 10px;color:var(--amber)}
.sub{color:var(--dim);font-size:11.5px;margin:4px 0 18px}
.grid{display:grid;gap:13px}.kpis{grid-template-columns:repeat(auto-fit,minmax(135px,1fr))}
.row2{grid-template-columns:3fr 2fr}.row2e{grid-template-columns:1fr 1fr}
@media(max-width:920px){.row2,.row2e{grid-template-columns:1fr}}
.card{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);
 border-radius:16px;padding:15px;box-shadow:0 6px 24px rgba(0,0,0,.35)}
.kpi{text-align:center;padding:12px 6px;background:var(--bg2);border:1px solid var(--line);
 border-radius:14px;position:relative;overflow:hidden}
.kpi::after{content:'';position:absolute;inset:0 0 auto 0;height:2px;
 background:linear-gradient(90deg,transparent,var(--amber),transparent)}
.kpi b{display:block;font-size:20px;margin-top:3px}
.kpi span{font-size:10.5px;color:var(--dim)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{padding:7px 8px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--dim2);font-weight:normal;font-size:11px;text-transform:uppercase;letter-spacing:.4px}
tr:hover td{background:rgba(255,178,36,.04)}
.pos{color:var(--pos)}.neg{color:var(--neg)}.ai{color:var(--ai)}
.chip{display:inline-block;padding:2.5px 10px;border-radius:99px;font-size:11px}
.c-bull{background:rgba(46,230,160,.12);color:var(--pos)}
.c-bear{background:rgba(255,101,101,.12);color:var(--neg)}
.c-neut{background:rgba(154,164,187,.12);color:var(--dim)}
.c-ai{background:rgba(180,139,255,.14);color:var(--ai)}
.c-amb{background:rgba(255,178,36,.14);color:var(--amber)}
canvas{width:100%;display:block}
button{background:linear-gradient(90deg,var(--amber),var(--amber2));color:#140b02;border:0;
 border-radius:11px;padding:9px 18px;font-size:13px;font-weight:700;cursor:pointer;margin-left:8px;
 box-shadow:0 4px 14px rgba(255,178,36,.25);transition:.2s}
button:hover{filter:brightness(1.1)}
button.warn{background:linear-gradient(90deg,#ff6565,#ff8a3d);color:#1a0505;box-shadow:0 4px 14px rgba(255,101,101,.25)}
button.ghost{background:var(--card2);color:var(--txt);border:1px solid var(--line2);box-shadow:none}
.tick{display:flex;gap:16px;overflow-x:auto;padding:5px 2px;font-size:12px;color:var(--dim)}
.tick b{color:var(--txt);font-size:12.5px}
.bar{height:5px;background:var(--line);border-radius:4px;overflow:hidden;width:64px;display:inline-block;vertical-align:middle}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--ai),var(--info))}
.summary{font-size:11.5px;color:var(--dim);max-width:430px;white-space:normal}
#toast{position:fixed;bottom:18px;right:18px;background:#151923;border:1px solid var(--amber);
 padding:10px 16px;border-radius:11px;display:none;font-size:13px;z-index:99;box-shadow:0 8px 30px rgba(0,0,0,.5)}
.pill{font-size:11px;padding:3px 10px;border-radius:99px;background:var(--bg2);border:1px solid var(--line);color:var(--dim)}
.mono{direction:ltr;font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--dim)}
pre.md{white-space:pre-wrap;font-size:11.5px;line-height:1.75;color:#c8d2e6;direction:ltr;text-align:left;
 max-height:70vh;overflow:auto;font-family:ui-monospace,Menlo,monospace}
a.cl{color:var(--info);text-decoration:none}
.nav-sec{margin-right:auto;font-size:11px;color:var(--dim2)}
.resbtns button{padding:6px 12px;font-size:12px;margin:0 0 0 6px}
.resbtns button.on{outline:2px solid var(--amber)}
.bigprice{font-size:30px;font-weight:800;letter-spacing:.5px}
.stat-row{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}
.stat-box{flex:1;min-width:110px;background:var(--bg2);border:1px solid var(--line);border-radius:12px;padding:10px;text-align:center}
.stat-box b{display:block;font-size:16px;margin-top:3px}
.stat-box span{font-size:10.5px;color:var(--dim)}
</style></head><body>
<nav>
 <span class="brand">⚡ MarketMind</span>
 <a href="#/overview" data-p="overview">🏠 نمای کل</a>
 <a href="#/markets" data-p="markets">📊 بازارها</a>
 <a href="#/research" data-p="research">🔬 تحقیق</a>
 <a href="#/settings" data-p="settings">⚙️ تنظیمات</a>
 <span class="nav-sec" id="livets">—</span>
</nav>
<div id="app"></div>
<div id="toast"></div>
<script>
/* ---------------- utils ---------------- */
const $=(h)=>{const d=document.createElement('div');d.innerHTML=h.trim();return d.firstChild};
const f=(x,d=2)=>x==null||x===undefined?'—':(+x).toFixed(d);
const cls=(x)=>+x>=0?'pos':'neg';
const sgn=(x,d=2)=>x==null?'—':(x>=0?'+':'')+f(x,d);
const fmtP=(p)=>p==null?'—':(+p).toLocaleString('en',{maximumFractionDigits:+p>=1000?1:(+p>=1?4:6)});
const faT=(ts)=>new Date(ts*1000).toLocaleString('fa-IR',{dateStyle:'short',timeStyle:'short'});
const faD=(ts)=>new Date(ts*1000).toLocaleDateString('fa-IR');
const biasChip=(b)=>b==='bullish'?'<span class="chip c-bull">صعودی</span>':b==='bearish'?'<span class="chip c-bear">نزولی</span>':'<span class="chip c-neut">خنثی</span>';
const tag=(m)=>m&&m.includes('llm_conviction')?'<span class="chip c-ai">🤖 AI</span>':'<span class="chip c-bull">قانون</span>';
function toast(t){const e=document.getElementById('toast');e.textContent=t;e.style.display='block';setTimeout(()=>e.style.display='none',4000)}
let MK={markets:{},ts:0}, SG={signals:{}}, ST=null;
async function jget(u){return (await fetch(u)).json()}
async function refreshLive(){
 try{[MK,SG]=await Promise.all([jget('/api/markets'),jget('/api/signals')]);
   document.getElementById('livets').textContent='لایو · '+
     Math.round((Date.now()/1000-MK.ts))+' ثانیه پیش';}catch(e){}
}
/* ---------------- router ---------------- */
const routes={overview:pgOverview,markets:pgMarkets,research:pgResearch,settings:pgSettings,coin:pgCoin};
let curRes='1D';
window.onhashchange=render;
function render(){
 const h=location.hash.replace('#/','')||'overview';
 const parts=h.split('/');
 const page=routes[parts[0]]?parts[0]:'overview';
 document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('on',a.dataset.p===page||(page==='coin'&&a.dataset.p==='markets')));
 routes[page](parts[1]);
}
/* ---------------- overview ---------------- */
async function pgOverview(){
 const app=document.getElementById('app');
 app.innerHTML=`<h1>🧠 نمای کل — معاملهٔ آزمایشی</h1>
 <div class="sub">قوانین قطعی + تحلیلگر AI · اهرم ۳ · سرمایهٔ فرضی ۲۰۰$ · کارمزد واقعی والکس</div>
 <div class="card" style="margin-bottom:13px"><div class="tick" id="tick"></div></div>
 <div id="ovbody"><div class="sub">در حال بارگذاری…</div></div>`;
 await refreshLive(); await ovFill();
}
async function ovFill(){
 const d=await jget('/api/state'); ST=d;
 document.getElementById('tick').innerHTML=Object.entries(MK.markets).map(([s,m])=>
  `<span>${s.replace('USDT','')} <b class="${cls(m.ch24)}">${fmtP(m.last)}</b>
   <small class="${cls(m.ch24)}">${sgn(m.ch24,1)}%</small></span>`).join('');
 const eqv=d.equity.length?d.equity[d.equity.length-1].value:d.cash;
 document.getElementById('ovbody').innerHTML=`
 <div class="grid kpis">
  <div class="kpi"><span>ارزش حساب</span><b>${f(eqv)}$</b></div>
  <div class="kpi"><span>سود/زیان</span><b class="${cls(eqv-200)}">${sgn(eqv-200)}$</b></div>
  <div class="kpi"><span>موقعیت باز</span><b>${d.positions.length}</b></div>
  <div class="kpi"><span>برد کل</span><b>${d.stats_all.n?d.stats_all.win+'%':'—'}</b></div>
  <div class="kpi"><span>قانون / AI</span><b>${d.stats_signal.n} / ${d.stats_llm.n}</b></div>
  <div class="kpi"><span>وضعیت</span><b>${d.halted?'<span class="neg">متوقف</span>':'<span class="pos">فعال</span>'}</b></div>
 </div>
 <div class="grid row2" style="margin-top:13px">
  <div class="card"><h3>📈 منحنی حساب</h3><canvas id="eqc" height="300"></canvas></div>
  <div class="card"><h3>🎛 کنترل</h3>
   <button onclick="doRun()">▶ اجرای چرخهٔ الان</button>
   <button class="warn" onclick="doHalt()">${d.halted?'▶ ادامه':'⛔ توقف'}</button>
   <div style="margin-top:12px;font-size:12px;color:var(--dim)">
   آخرین تصمیم: ${d.decisions.length?faT(d.decisions[0].ts):'—'}<br>
   برد قانون: <b>${d.stats_signal.win||0}%</b> · انتظاری ${sgn(d.stats_signal.exp)}% ·
   PF ${f(d.stats_signal.pf)}<br>
   برد AI: <b>${d.stats_llm.win||0}%</b> · انتظاری ${sgn(d.stats_llm.exp)}% ·
   PF ${f(d.stats_llm.pf)}</div>
   <div style="margin-top:10px;font-size:11px;color:var(--dim2)">هر روز ۴:۳۰ خودکار. AI فقط وتو/کوچک‌سازی/ورود کوچک؛ خروج همیشه قانون قطعی.</div>
  </div>
 </div>
 <div class="grid row2" style="margin-top:13px">
  <div class="card"><h3>📂 موقعیت‌های باز</h3><table><thead><tr>
   <th>نماد</th><th>نوع</th><th>ورود</th><th>لایو</th><th>PnL%</th><th>لیکویید</th><th>فاصله</th><th>وثیقه</th></tr></thead>
   <tbody>${d.positions.map(p=>{const cur=(MK.markets[p.symbol]||{}).last;
     const pnl=cur?((cur/p.entry_px-1)*(1+p.leverage)*100):null;
     const dist=cur?((cur/p.liq_px-1)*100):null;
     return `<tr style="cursor:pointer" onclick="location.hash='#/coin/${p.symbol}'">
     <td><b>${p.symbol}</b></td><td>${tag(p.meta)}</td><td>${fmtP(p.entry_px)}</td>
     <td>${cur?fmtP(cur):'—'}</td><td class="${cls(pnl||0)}">${pnl==null?'—':sgn(pnl,1)}</td>
     <td>${fmtP(p.liq_px)}</td><td class="${dist!=null&&dist<15?'neg':''}">${dist==null?'—':f(dist,1)+'%'}</td>
     <td>${f(p.collateral,1)}$</td></tr>`}).join('')||'<tr><td colspan=8>—</td></tr>'}</tbody></table></div>
  <div class="card"><h3>🤖 تحلیلگر AI — آخرین نظر</h3><table><thead><tr>
   <th>نماد</th><th>گرایش</th><th>اطمینان</th><th>سطوح</th><th>خلاصه</th></tr></thead><tbody>
   ${(d.analyst||[]).map(a=>`<tr><td><b>${a.symbol}</b></td><td>${biasChip(a.bias)}</td>
    <td><span class="bar"><i style="width:${Math.round((a.confidence||0)*100)}%"></i></span> ${Math.round((a.confidence||0)*100)}%</td>
    <td class="mono" style="font-size:10px">${a.support||'—'}<br>${a.resistance||'—'}</td>
    <td class="summary">${a.summary||''}</td></tr>`).join('')||'<tr><td colspan=5>—</td></tr>'}</tbody></table></div>
 </div>
 <div class="grid row2" style="margin-top:13px">
  <div class="card"><h3>📒 معاملات بسته‌شده</h3><table><thead><tr>
   <th>نماد</th><th>نوع</th><th>ورود</th><th>خروج</th><th>بازده%</th><th>کارمزد</th><th>دلیل</th></tr></thead>
   <tbody>${d.trades.map(t=>`<tr><td><b>${t.symbol}</b></td><td>${tag(t.meta)}</td>
    <td>${fmtP(t.entry_px)}</td><td>${fmtP(t.exit_px)}</td>
    <td class="${cls(t.return_pct)}">${sgn(t.return_pct,1)}</td><td>${f(t.fees)}$</td><td>${t.close_reason}</td></tr>`).join('')
    ||'<tr><td colspan=7>هنوز معامله‌ای بسته نشده</td></tr>'}</tbody></table></div>
  <div class="card"><h3>🧾 جریان تصمیم‌ها</h3><table><thead><tr>
   <th>زمان</th><th>نماد</th><th>اقدام</th><th>جزئیات</th></tr></thead><tbody>
   ${d.decisions.slice(0,22).map(x=>`<tr><td>${faT(x.ts)}</td><td>${x.symbol}</td>
    <td>${{open:'🟢',close:'🔒',skip:'⏭',liquidate:'⛔',hold:'⏸'}[x.action]||''} ${x.action}</td>
    <td dir="ltr" class="mono" style="max-width:330px;white-space:normal">${x.detail}</td></tr>`).join('')
    ||'<tr><td colspan=4>—</td></tr>'}</tbody></table></div>
 </div>`;
 drawEq(d.equity);
}
function drawEq(eq){
 const c=document.getElementById('eqc'); if(!c)return; if(eq.length<2){
  const x=c.getContext('2d');c.width=c.offsetWidth*2;c.height=340;
  x.fillStyle='#6b7490';x.font='24px system-ui';x.fillText('هنوز داده‌ای نیست — بعد از چرخه‌های بعدی',20,170);return;}
 const ctx=c.getContext('2d');const W=c.width=c.offsetWidth*2,H=c.height=340;
 const vals=eq.map(e=>e.value);const mn=Math.min(...vals,199),mx=Math.max(...vals,201);
 const X=i=>30+i*(W-60)/(vals.length-1),Y=v=>H-34-(v-mn)*(H-70)/((mx-mn)||1);
 ctx.strokeStyle='#1f2431';ctx.setLineDash([9,7]);ctx.beginPath();ctx.moveTo(30,Y(200));ctx.lineTo(W-30,Y(200));ctx.stroke();ctx.setLineDash([]);
 const g=ctx.createLinearGradient(0,0,W,0);g.addColorStop(0,vals[vals.length-1]>=200?'#2ee6a0':'#ff6565');
 g.addColorStop(1,vals[vals.length-1]>=200?'#69c3ff':'#ffb224');
 ctx.strokeStyle=g;ctx.lineWidth=4.5;ctx.beginPath();
 vals.forEach((v,i)=>i?ctx.lineTo(X(i),Y(v)):ctx.moveTo(X(i),Y(v)));ctx.stroke();
 ctx.fillStyle='#9aa4bb';ctx.font='21px system-ui';ctx.fillText('شروع ۲۰۰$',36,Y(200)-10);
 const lv=vals[vals.length-1];ctx.fillStyle=lv>=200?'#2ee6a0':'#ff6565';ctx.font='bold 27px system-ui';
 ctx.fillText(lv.toFixed(2)+' USDT',30,38);
}
/* ---------------- markets ---------------- */
async function pgMarkets(){
 const app=document.getElementById('app');
 app.innerHTML=`<h1>📊 بازارها</h1><div class="sub">قیمت‌های لایو والکس + وضعیت رژیم + نظر تحلیلگر AI — روی هر ردیف کلیک کن</div>
 <div id="mkt" class="card"><div class="sub">…</div></div>`;
 await refreshLive(); await mktFill();
}
let mktSort={k:null,desc:false};
function sortMkt(k){mktSort={k,desc:mktSort.k===k?!mktSort.desc:false};mktFill()}
async function mktFill(){
 let ents=Object.entries(MK.markets);
 if(mktSort.k){const k=mktSort.k;const val=(sym,m)=>{
   if(k==='sym')return sym;
   if(k==='er')return (SG.signals[sym]||{}).er??-9;
   if(k==='ret20_pct')return (SG.signals[sym]||{}).ret20_pct??-9;
   return m[k]??-9;};
  ents.sort((a,b)=>{const va=val(a[0],a[1]),vb=val(b[0],b[1]);
   return (typeof va==='string')?va.localeCompare(vb)*(mktSort.desc?-1:1)
     :(mktSort.desc?vb-va:va-vb);});}
 const rows=ents.map(([sym,m])=>{
  const s=SG.signals[sym]||{};
  if(sym==='USDTTMN'){
   return `<tr style="cursor:pointer" onclick="location.hash='#/coin/USDTTMN'">
   <td><b>تتر/تومان</b></td><td class="bigprice" style="font-size:15px">${(+m.last).toLocaleString('en',{maximumFractionDigits:0})}</td>
   <td class="${cls(m.ch24)}">${sgn(m.ch24,1)}%</td>
   <td>${(+m.vol24_quote/1e9).toFixed(1)}B</td>
   <td colspan=3><span class="pill">پرمیوم z: ${s.prem_z!=null?sgn(s.prem_z):'—'}</span></td>
   <td>${s.ai_bias?biasChip(s.ai_bias):'—'}</td><td>—</td></tr>`;
  }
  const above=s.above_sma200;
  return `<tr style="cursor:pointer" onclick="location.hash='#/coin/${sym}'">
   <td><b>${sym.replace('USDT','')}</b><span class="mono" style="font-size:9px"> /USDT</span></td>
   <td><b>${fmtP(m.last)}</b></td>
   <td class="${cls(m.ch24)}">${sgn(m.ch24,1)}%</td>
   <td>${(m.vol24_quote/1e6).toFixed(1)}M</td>
   <td>${above==null?'—':above?'<span class="chip c-bull">بالای SMA200</span>':'<span class="chip c-bear">زیر SMA200</span>'}</td>
   <td>${s.er!=null?f(s.er):'—'}</td>
   <td class="${cls(s.ret20_pct)}">${s.ret20_pct!=null?sgn(s.ret20_pct,0)+'%':'—'}</td>
   <td>${s.ai_bias?biasChip(s.ai_bias):'—'}</td>
   <td>${above==null?'—':above&&s.er>=0.3?'<span class="chip c-amb"> آماده ورود</span>':''}</td></tr>`;
 }).join('');
 document.getElementById('mkt').innerHTML=`<table><thead><tr>
  <th onclick="sortMkt('sym')">نماد ⌄</th><th onclick="sortMkt('last')">قیمت لایو ⌄</th>
  <th onclick="sortMkt('ch24')">۲۴ساعت ⌄</th><th onclick="sortMkt('vol24_quote')">حجم ۲۴س ⌄</th>
  <th>رژیم (SMA200)</th><th onclick="sortMkt('er')">ER ⌄</th>
  <th onclick="sortMkt('ret20_pct')">بازده ۲۰روز ⌄</th><th>تحلیل AI</th><th>سیگنال</th></tr></thead><tbody>${rows}</tbody></table>
  <div style="margin-top:8px;font-size:11px;color:var(--dim2)">حجم: دلار/روز — ER نزدیک ۱ = روند تمیز. «آماده ورود» یعنی شرط‌های قانون ورود فعال است. روی سرستون‌ها برای مرتب‌سازی کلیک کن.</div>`;
}
/* ---------------- coin detail ---------------- */
async function pgCoin(sym){
 if(!sym)sym='BTCUSDT';
 const app=document.getElementById('app');
 app.innerHTML=`<h1 id="ct">🕯 ${sym}</h1>
 <div class="sub">کندل‌های واقعی والکس · SMA50/200 · حجم · شاخص‌ها · نظر AI</div>
 <div class="card" style="margin-bottom:13px"><div class="resbtns">
  <button onclick="setRes('60', '${sym}')">۱ ساعت</button>
  <button onclick="setRes('240','${sym}')">۴ ساعت</button>
  <button onclick="setRes('1D','${sym}')">روزانه</button>
  <span id="cstat" class="pill" style="margin-right:8px"></span></div>
  <canvas id="cc" height="420" style="margin-top:10px"></canvas></div>
 <div id="cbody"><div class="sub">…</div></div>`;
 await coinFill(sym,curRes);
}
function setRes(r,sym){curRes=r;document.querySelectorAll('.resbtns button').forEach(b=>
 b.classList.toggle('on',b.textContent.includes({'60':'۱','240':'۴','1D':'روزانه'}[r])));coinFill(sym,r)}
async function coinFill(sym,res){
 try{
  const d=await jget(`/api/coin/${sym}?res=${res}`);
  const live=d.live;
  document.getElementById('cstat').innerHTML=
   live?`لایو: <b>${fmtP(live.last)}</b> · ۲۴س <span class="${cls(live.ch24)}">${sgn(live.ch24,1)}%</span>
   · ح:${fmtP(live.high24)} ک:${fmtP(live.low24)}`:'—';
  drawCandles(d);
  const s=SG.signals[sym]||{}; const a=d.analyst; const p=d.position;
  document.getElementById('cbody').innerHTML=`
  <div class="stat-row">
   <div class="stat-box"><span>ER(20)</span><b>${d.ind.er!=null?f(d.ind.er):'—'}</b></div>
   <div class="stat-box"><span>ATR٪</span><b>${d.ind.atr_pct!=null?f(d.ind.atr_pct,1)+'%':'—'}</b></div>
   <div class="stat-box"><span>SMA200</span><b class="${s.above_sma200?'pos':'neg'}">${s.above_sma200==null?'—':(s.above_sma200?'بالا':'پایین')}</b></div>
   <div class="stat-box"><span>بازده ۲۰روز</span><b class="${cls(s.ret20_pct)}">${s.ret20_pct!=null?sgn(s.ret20_pct,0)+'%':'—'}</b></div>
   ${p?`<div class="stat-box"><span>موقعیت باز ما</span><b>${tag(p.meta)}</b></div>`:''}
  </div>
  <div class="grid row2">
   <div class="card"><h3>🤖 نظر تحلیلگر AI دربارهٔ این کوین</h3>
   ${a?`<div style="font-size:13px">${biasChip(a.bias)}
    <span class="bar"><i style="width:${Math.round((a.confidence||0)*100)}%"></i></span> اطمینان ${Math.round((a.confidence||0)*100)}%
    ${a.entry_ok?'<span class="chip c-bull">ورید OK</span>':''}
    <div class="mono" style="margin-top:8px">حمایت: ${a.support||'—'} · مقاومت: ${a.resistance||'—'}</div>
    <p class="summary" style="margin-top:8px;font-size:12.5px">${a.summary||''}</p>
    <div class="mono" style="color:var(--dim2)">${faT(a.ts)}</div></div>`:'<div class="sub">هنوز نظری ثبت نشده</div>'}</div>
   <div class="card"><h3>🗂 سابقهٔ نظرهای AI</h3>
   <table><thead><tr><th>تاریخ</th><th>گرایش</th><th>اطمینان</th><th>خلاصه</th></tr></thead>
   <tbody>${(d.analyst_hist||[]).map(h=>`<tr><td>${faT(h.ts)}</td><td>${biasChip(h.bias)}</td>
    <td>${Math.round((h.confidence||0)*100)}%</td><td class="summary">${h.summary||''}</td></tr>`).join('')
    ||'<tr><td colspan=4>—</td></tr>'}</tbody></table></div>
   <div class="card"><h3>📒 معاملات آزمایشی این کوین</h3>
   <table><thead><tr><th>ورود</th><th>خروج</th><th>بازده%</th><th>دلیل</th><th>نوع</th></tr></thead>
   <tbody>${d.trades.map(t=>`<tr><td>${fmtP(t.entry_px)}</td><td>${fmtP(t.exit_px)}</td>
    <td class="${cls(t.return_pct)}">${sgn(t.return_pct,1)}</td><td>${t.close_reason}</td><td>${tag(t.meta)}</td></tr>`).join('')
    ||'<tr><td colspan=5>—</td></tr>'}</tbody></table></div>
  </div>`;
 }catch(e){document.getElementById('cbody').innerHTML='<div class="sub">خطا در دریافت داده</div>'}
}
function drawCandles(d){
 const c=document.getElementById('cc');if(!c)return;
 const ctx=c.getContext('2d');const W=c.width=c.offsetWidth*2,H=c.height=840;
 ctx.clearRect(0,0,W,H);
 const cs=d.candles;if(!cs.length)return;
 const n=cs.length,padL=64,padR=16,padT=20,chH=H*0.72,volY=chH+46,volH=H-volY-20;
 const mn=Math.min(...cs.map(x=>x.low)),mx=Math.max(...cs.map(x=>x.high));
 const Y=v=>padT+(mx-v)/(mx-mn||1)*(chH-padT);
 const bw=(W-padL-padR)/n,cw=Math.max(1.5,bw*0.62);
 const maxV=Math.max(...cs.map(x=>x.volume||0));
 ctx.strokeStyle='#1a1f2b';ctx.lineWidth=1;
 for(let i=0;i<=5;i++){const v=mn+(mx-mn)*i/5,y=Y(v);
  ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(W-padR,y);ctx.stroke();
  ctx.fillStyle='#6b7490';ctx.font='19px ui-monospace';
  ctx.fillText(fmtP(v),4,y+6);}
 let hover=null;
 cs.forEach((x,i)=>{
  const cx=padL+bw*(i+0.5);
  const up=x.close>=x.open;
  ctx.strokeStyle=up?'#2ee6a0':'#ff6565';ctx.fillStyle=up?'rgba(46,230,160,.85)':'rgba(255,101,101,.85)';
  ctx.lineWidth=1.4;
  ctx.beginPath();ctx.moveTo(cx,Y(x.high));ctx.lineTo(cx,Y(x.low));ctx.stroke();
  const yo=Y(x.open),yc=Y(x.close);
  const top=Math.min(yo,yc),hh=Math.max(1.6,Math.abs(yc-yo));
  ctx.fillRect(cx-cw/2,top,cw,hh);
  const vh=(x.volume||0)/(maxV||1)*volH;
  ctx.fillStyle=up?'rgba(46,230,160,.35)':'rgba(255,101,101,.35)';
  ctx.fillRect(cx-cw/2,H-20-vh,cw,vh);
  if(i%Math.ceil(n/8)===0){ctx.fillStyle='#6b7490';ctx.font='18px system-ui';
   ctx.textAlign='center';ctx.fillText(faD(x.ts),cx,H-2);ctx.textAlign='start';}
 });
 const drawLine=(arr,color)=>{if(!arr)return;ctx.strokeStyle=color;ctx.lineWidth=2.2;ctx.beginPath();let started=false;
  arr.forEach((v,i)=>{if(v==null)return;const cx=padL+bw*(i+0.5);
   if(!started){ctx.moveTo(cx,Y(v));started=true}else ctx.lineTo(cx,Y(v));});ctx.stroke();};
 drawLine(d.sma50,'#69c3ff');drawLine(d.sma200,'#ffb224');
 ctx.fillStyle='#69c3ff';ctx.font='19px system-ui';ctx.fillText('SMA50',W-150,padT+22);
 ctx.fillStyle='#ffb224';ctx.fillText('SMA200',W-150,padT+46);
 c.onmousemove=(e)=>{const r=c.getBoundingClientRect();const x=(e.clientX-r.left)*(W/r.width);
  const i=Math.floor((x-padL)/bw);if(i>=0&&i<n){const k=cs[i];
   c.title=`${faD(k.ts)}\nO:${k.open} H:${k.high} L:${k.low} C:${k.close} V:${Math.round(k.volume)}`;}};
}
/* ---------------- research ---------------- */
async function pgResearch(){
 const app=document.getElementById('app');
 app.innerHTML=`<h1>🔬 دفترچهٔ تحقیق</h1>
 <div class="sub">۱۶ آزمایش ثبت‌شده از E001 تا E016 — با نتیجهٔ صادقانهٔ هر کدام</div>
 <div class="card"><pre class="md" id="md">در حال بارگذاری…</pre></div>`;
 const d=await jget('/api/research');
 document.getElementById('md').textContent=d.text;
}
/* ---------------- settings ---------------- */
async function pgSettings(){
 const app=document.getElementById('app');
 app.innerHTML=`<h1>⚙️ تنظیمات و وضعیت</h1><div class="sub">اتصال‌ها، کلیدها و کنترل‌ها (اسرار هرگز نمایش داده نمی‌شوند — فقط وضعیت)</div>
 <div id="sbody" class="card">…</div>`;
 const s=await jget('/api/settings');
 document.getElementById('sbody').innerHTML=`
 <table><tbody>
 <tr><td>🔗 تحلیلگر AI (LLM)</td><td>${s.llm?'<span class="chip c-bull">متصل</span>':'<span class="chip c-bear">غیرفعال</span>'}</td>
     <td class="mono">data/secrets/llm_api.json</td></tr>
 <tr><td>📨 تلگرام</td><td>${s.telegram?'<span class="chip c-bull">تنظیم‌شده</span>':'<span class="chip c-neut">تنظیم نشده</span>'}</td>
     <td class="mono">data/secrets/telegram.json</td></tr>
 <tr><td>🔑 کلید والکس</td><td>${s.wallex_key?'<span class="chip c-amb">موجود (قفل نردبان فعال)</span>':'<span class="chip c-neut">موجود نیست</span>'}</td>
     <td class="mono">data/secrets/wallex_api_key.txt</td></tr>
 <tr><td>🛡 ورود داشبورد</td><td>${s.dashboard_login?'<span class="chip c-bull">یوزر/پس فعال</span>':'<span class="chip c-neut">فقط لوکال</span>'}</td>
     <td class="mono">data/secrets/dashboard_login.json</td></tr>
 <tr><td>⛔ وضعیت معامله‌گری</td><td>${s.halted?'<span class="chip c-bear">متوقف</span>':'<span class="chip c-bull">فعال</span>'}</td><td>—</td></tr>
 </tbody></table>
 <div style="margin-top:14px">
  <button onclick="doRun()">▶ اجرای چرخهٔ الان</button>
  <button class="warn" onclick="doHalt()">${s.halted?'▶ ادامه':'⛔ توقف'}</button>
  <button class="ghost" onclick="notifyTest()">📨 تست تلگرام</button>
 </div>
 <div style="margin-top:14px;font-size:11.5px;color:var(--dim2)">
 قرارداد پول واقعی: ۸ هفته+ paper موفق → دوزیرهٔ شواهد → تأیید صریح مالک.
 کلید والکس فقط-معامله، بدون مجوز برداشت، با IP محدود.
 </div>`;
}
/* ---------------- actions ---------------- */
async function doRun(){
 const d0=await jget('/api/state');const t0=d0.decisions.length?d0.decisions[0].ts:0;
 toast('چرخه شروع شد…');
 await fetch('/api/run',{method:'POST'});
 let tries=0;const iv=setInterval(async()=>{
  tries++;const d=await jget('/api/state');
  if(d.decisions.length&&d.decisions[0].ts>t0){clearInterval(iv);toast('چرخه تمام شد ✓');render();}
  else if(tries>60){clearInterval(iv);toast('پایان انتظار');render();}
 },5000)}
async function doHalt(){const s=await jget('/api/settings');
 await fetch(s.halted?'/api/resume':'/api/halt',{method:'POST'});
 toast(s.halted?'ادامه یافت':'متوقف شد');render()}
async function notifyTest(){const r=await (await fetch('/api/notify-test',{method:'POST'})).json();
 toast(r.sent?'پیام تلگرام ارسال شد ✓':'ارسال نشد — تنظیمات/شبکه را چک کن')}
/* ---------------- boot ---------------- */
refreshLive();setInterval(refreshLive,15000);setInterval(()=>{if(curPageNeeds())render()},60000);
function curPageNeeds(){return location.hash.includes('overview')||location.hash.includes('markets')}
render();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(PAGE)
