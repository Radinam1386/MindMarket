"""LLM market analyst — reads live Wallex data tables, returns structured opinions.

The analyst sees: 40 daily bars OHLCV, SMA200/ER/ATR/vol state, the USDT/TMN
premium z (Wallex-native macro gauge), and the paper account's open position.
It answers in strict JSON. It NEVER sees fake data and its words are always
stored alongside the deterministic signal for later scoring (was the LLM right?).
"""
from __future__ import annotations

import json

import pandas as pd

from .. import llm
from ..backtest.strategies import efficiency_ratio

SYSTEM = """تو یک تحلیلگر ارشد بازار رمزارز در صرافی والکس (بازار ایران) هستی.
فقط بر اساس داده‌ای که در پیام می‌بینی قضاوت کن. هرگز عدد نساز و اخبار ادعایی نقل نکن.
خروجی فقط JSON با این ساختار باشد:
{"bias":"bullish|bearish|neutral",
 "confidence":0.0,
 "entry_ok":true|false,
 "size_factor":0.0,
 "key_levels":{"support":0.0,"resistance":0.0},
 "summary":"دو جمله فارسی"}
قواعد: entry_ok فقط وقتی true که دلایل فنی کافی برای ورود امروز ببینی.
size_factor بین 0.1 تا 1.0 (میزان اطمینان به سایز پوزیشن).
confidence صادقانه، نه خوش‌بینانه. خلاصه کوتاه و مستدل.
اولین کاراکتر پاسخ باید {{ باشد و آخرین }} — هیچ متن، توضیح یا markdown اضافه نکن."""

PREMIUM_HELP = (
    "premium_z: z-score تغییر ۶۰روزه نرخ تتر/تومان (دیروز). مقدار بالای +2 یعنی استرس و "
    "ریسک‌گریزی در بازار ایران (معمولاً bearish برای کریپتو)، زیر -2 یعنی اشتها به ریسک (bullish)."
)


def build_context(symbol: str, df: pd.DataFrame, prem_z: float | None,
                  position: dict | None, cfg: dict,
                  memory: list[dict] | None = None) -> str:
    tail = df.tail(40)
    close = df["close"]
    sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
    er = float(efficiency_ratio(close, 20).iloc[-1])
    atr = float((df["high"] - df["low"]).rolling(14).mean().iloc[-1])
    ret20 = float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) > 21 else 0.0
    vol30 = float(close.pct_change().rolling(30).std().iloc[-1] * 100)

    rows = "\n".join(
        f"{pd.Timestamp(t, unit='s', tz='UTC').date()} O:{o:.6g} H:{h:.6g} L:{l:.6g} "
        f"C:{c:.6g} V:{v:.0f}"
        for t, o, h, l, c, v in zip(tail["ts"], tail["open"], tail["high"],
                                    tail["low"], tail["close"], tail["volume"])
    )
    pos_txt = "بدون موقعیت باز" if position is None else (
        f"موقعیت LONG باز: ورود {position['entry_px']:.6g}، وثیقه {position['collateral']:.1f}$، "
        f"لیکویید {position['liq_px']:.6g}")
    prem_line = "" if prem_z is None else f"- {PREMIUM_HELP}\n  مقدار فعلی: {prem_z:+.2f}"
    if memory:
        lines = ["سابقهٔ نظرهای قبلی خودت و آنچه بعدش شد (بازده قیمت از آن روز تا امروز):"]
        for m in memory:
            lines.append(f"- {pd.Timestamp(m['date'], unit='s', tz='UTC').date()}: "
                         f"گفتی {m['bias']} با اطمینان {m['confidence']:.0%} → "
                         f"قیمت از آن زمان {m['outcome_pct']:+.1f}%")
        mem_block = "\n".join(lines) + "\n(از این سابقه بیاموز: اگر مدام اشتباه گفته‌ای محتاط‌تر باش.)"
    else:
        mem_block = ""

    return f"""نماد: {symbol} (بازار تعهدی والکس، جفت USDT)
قیمت روزهای اخیر (UTC):
{rows}

شاخص‌ها:
- قیمت فعلی: {float(close.iloc[-1]):.6g}
- SMA200: {sma200:.6g} (قیمت {'بالای' if close.iloc[-1] > sma200 else 'زیر'} میانگین)
- Efficiency Ratio(20): {er:.2f} (نزدیک 1 = روند تمیز، نزدیک 0 = رنج)
- ATR(14): {atr:.6g} (~{atr / close.iloc[-1] * 100:.1f}% قیمت)
- بازده 20روزه: {ret20 * 100:+.1f}%
- نوسان روزانه 30روزه: {vol30:.2f}%
{prem_line}
{mem_block}
وضعیت حساب ما: {pos_txt}
سیگنال قطعی سیستم (کانال دونچین 20 + فیلترها): ورود={'بله' if cfg['entry_sig'] else 'خیر'}، خروج={'بله' if cfg['exit_sig'] else 'خیر'}
محدودیت‌های سخت: حداکثر اهرم 3، حداکثر ریسک هر معامله 2% سرمایه، سقف ارزش موقعیت 1700$.
JSON تحلیلی‌ات را بده."""


def analyze(symbol: str, df: pd.DataFrame, prem_z: float | None,
            position: dict | None, cfg: dict, memory: list[dict] | None = None) -> dict:
    """One structured opinion. Never raises; returns neutral fallback on failure."""
    try:
        raw = llm.chat(SYSTEM, build_context(symbol, df, prem_z, position, cfg, memory),
                       temperature=0.2, max_tokens=1500)
        data = llm.parse_json_loose(raw)
        bias = data.get("bias", "neutral")
        if bias not in ("bullish", "bearish", "neutral"):
            bias = "neutral"
        conf = min(1.0, max(0.0, float(data.get("confidence", 0.0))))
        size = min(1.0, max(0.1, float(data.get("size_factor", 0.5))))
        return {
            "bias": bias,
            "confidence": conf,
            "entry_ok": bool(data.get("entry_ok", False)),
            "size_factor": size,
            "key_levels": data.get("key_levels", {}),
            "summary": str(data.get("summary", ""))[:500],
            "ok": True,
        }
    except (llm.LLMError, ValueError, KeyError) as e:
        return {"bias": "neutral", "confidence": 0.0, "entry_ok": False,
                "size_factor": 0.0, "key_levels": {}, "summary": f"تحلیل ناموفق: {e}",
                "ok": False}


def premium_z_now(prem_df: pd.DataFrame) -> float | None:
    if prem_df is None or len(prem_df) < 70:
        return None
    ret = prem_df["close"].pct_change()
    z = (ret - ret.rolling(60).mean()) / ret.rolling(60).std()
    v = z.shift(1).iloc[-1]
    return float(v) if v == v else None
