"""On-demand, advisory-only multi-perspective review of stored Wallex candles.

The upstream trader skills target US equities; these roles adapt the review idea
for the data we actually have. No role can submit or approve an order.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

from .. import llm
from . import state

_lock = threading.Lock()
_jobs = {}
_active = False
RETRY_SECONDS = 300
MAX_AGE_SECONDS = 86400

ROLES = (
    ("trend", "روند", "جهت روند و فاصله از میانگین بلندمدت را بررسی کن."),
    ("momentum", "شتاب", "شتاب و تغییرات قیمت اخیر را بررسی کن."),
    ("volatility", "نوسان", "نوسان، دامنه کندل‌ها و پیامد آن برای ریسک را بررسی کن."),
    ("levels", "سطوح قیمت", "حمایت و مقاومت احتمالی را فقط با استناد به کندل‌ها بررسی کن."),
    ("volume", "حجم", "حجم ثبت‌شده و محدودیت تفسیر آن را بررسی کن."),
    ("regime", "وضعیت بازار", "رونددار یا خنثی بودن بازار و عدم قطعیت را بررسی کن."),
    ("entry", "شرایط ورود", "شرایط لازم برای بررسی ورود و شواهد مخالف را توضیح بده."),
    ("exit", "شرایط خروج", "سناریوهای ابطال و خروج را با توجه به داده موجود بررسی کن."),
    ("risk", "ریسک", "ریسک‌های معامله، کیفیت داده و نکات احتیاطی را بررسی کن."),
    ("critic", "منتقد", "مهم‌ترین استدلال مخالف و داده‌های غایب را پیدا کن."),
)

SYSTEM = """تو عضو یک تیم پژوهش بازار رمزارز هستی. فقط از داده‌های داده‌شده استفاده کن؛
هر داده غایب یا قدیمی و هر عدم قطعیت را صریح بگو. خبر، داده زنده، معامله،
موجودی یا عددی که در ورودی نیست اختراع نکن. تحلیل تو صرفاً مشورتی است.
فقط JSON بده: {"summary":"متن کوتاه فارسی", "stance":"bullish|bearish|neutral|unknown"}.
"""


def _review(role, context):
    key, name, task = role
    try:
        raw = llm.chat(SYSTEM, f"نقش: {name}\nوظیفه: {task}\n{context}",
                       max_tokens=1200, timeout=45)
        value = llm.parse_json_loose(raw)
        summary = value.get("summary")
        stance = value.get("stance", "unknown")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("empty summary")
        if stance not in ("bullish", "bearish", "neutral", "unknown"):
            stance = "unknown"
        return {"id": key, "name": name, "status": "ok", "stance": stance,
                "summary": summary.strip()[:800]}
    except Exception:
        # Do not return provider errors, credentials, or partial output to users.
        return {"id": key, "name": name, "status": "error", "stance": "unknown",
                "summary": "تحلیل این نقش دریافت نشد."}


def make_context(symbol, df):
    if len(df) < 200:
        raise ValueError("دادهٔ روزانه برای تحلیل کافی نیست.")
    close = df["close"]
    if float(close.iloc[-1]) <= 0:
        raise ValueError("قیمت پایانی معتبر نیست.")
    rows = "\n".join(
        f"{int(r.ts)} O:{r.open:.6g} H:{r.high:.6g} L:{r.low:.6g} C:{r.close:.6g} V:{r.volume:.6g}"
        for r in df.tail(40).itertuples()
    )
    return (f"نماد: {symbol}\nکندل‌های روزانه ذخیره‌شده والکس، زمان هر کندل یونیکس UTC است؛ "
            "این داده لزوماً زنده نیست.\n" + rows +
            f"\nمیانگین ۲۰۰ کندل پایانی: {float(close.tail(200).mean()):.6g}")


def run_team(symbol, df):
    """Run ten independent specialists followed by one synthesis; no trading side effects."""
    context = make_context(symbol, df)
    with ThreadPoolExecutor(max_workers=3) as pool:
        reports = list(pool.map(lambda role: _review(role, context), ROLES))
    source = "\n".join(f"{r['name']} ({r['status']}): {r['summary']}" for r in reports)
    coordinator = _review(("coordinator", "جمع‌بندی", "نظرهای موجود را جمع‌بندی کن؛ "
                           "اختلاف‌ها و نقش‌های ناموفق را روشن بگو."),
                          context + "\nنظرهای تیم:\n" + source)
    return {"symbol": symbol, "candle_ts": int(df["ts"].iloc[-1]),
            "created_at": int(time.time()), "reports": reports + [coordinator]}


def saved_review(conn, symbol, candle_ts):
    """Only a recent review of the same decision candle can affect an entry."""
    raw = state.get_setting(conn, "team_review:" + symbol)
    if not raw:
        return None
    try:
        result = json.loads(raw)
        if (result["candle_ts"] != int(candle_ts) or
                not 0 <= time.time() - result["created_at"] <= MAX_AGE_SECONDS):
            return None
        return result
    except (TypeError, ValueError, KeyError):
        return None


def entry_gate(result, side):
    """Fail closed on incomplete reviews; a team can only veto a paper entry."""
    if not result or not isinstance(result.get("reports"), list):
        return "unavailable"
    reports = result["reports"]
    specialists = {r.get("id"): r for r in reports if isinstance(r, dict)}
    if any(specialists.get(key, {}).get("status") != "ok" for key, _, _ in ROLES):
        return "unavailable"
    if specialists.get("coordinator", {}).get("status") != "ok":
        return "unavailable"
    if sum(specialists[key].get("stance") in ("bullish", "bearish", "neutral")
           for key, _, _ in ROLES) < 6:
        return "unavailable"
    opposite = "bearish" if side == "long" else "bullish"
    if sum(specialists[key].get("stance") == opposite for key, _, _ in ROLES) >= 3:
        return "blocked"
    return "allow"


def latest_job(account):
    with _lock:
        return dict(_jobs.get(account, {"status": "empty"}))


def start_review(account, symbol, frame, source="manual"):
    """Start one bounded background review; never block the trading cycle."""
    global _active
    make_context(symbol, frame)
    started_at = int(time.time())
    with _lock:
        if _active:
            return "busy"
        previous = _jobs.get(account, {})
        if started_at - previous.get("started_at", 0) < RETRY_SECONDS:
            return "cooldown"
        _active = True
        _jobs[account] = {"status": "running", "symbol": symbol,
                          "started_at": started_at, "source": source}

    def work():
        global _active
        try:
            result = run_team(symbol, frame)
            with state.use_account(account):
                conn = state.connect()
                try:
                    state.set_setting(conn, "team_review:" + symbol,
                                      json.dumps(result, ensure_ascii=False))
                    state.set_setting(conn, "team_review:last", json.dumps(result, ensure_ascii=False))
                finally:
                    conn.close()
            job = {"status": "complete", "symbol": symbol, "started_at": started_at,
                   "source": source, "result": result}
        except Exception:
            job = {"status": "error", "symbol": symbol, "started_at": started_at,
                   "source": source, "detail": "اجرای تحلیل تیمی ناموفق بود."}
        with _lock:
            _jobs[account] = job
            _active = False

    try:
        threading.Thread(target=work, daemon=True, name="mm-agent-team").start()
    except Exception:
        with _lock:
            _active = False
            _jobs[account] = {"status": "error", "started_at": started_at}
        raise
    return "started"
