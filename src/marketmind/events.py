"""Dashboard-visible event journal — in-memory ring buffer + best-effort file log.

Everything the operator should see (cycle starts/ends, HALT blocks, LLM failures,
data errors, auto-halt risk warnings) goes through record() here and shows up on
the dashboard's 📜 لاگ page via GET /api/logs. Recording must never raise.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from .config import LOGS_DIR

_LOCK = threading.Lock()
_RING: deque = deque(maxlen=200)
_FILE = LOGS_DIR / "events.log"


def record(level: str, source: str, message: str, detail: str = "") -> None:
    """level: info | warn | error. Never raises."""
    try:
        entry = {"ts": int(time.time()), "level": level, "source": source,
                 "message": (message or "")[:500], "detail": (detail or "")[:1500]}
        with _LOCK:
            _RING.append(entry)
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            with _FILE.open("a", encoding="utf-8") as fh:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(entry["ts"]))
                fh.write(f"{stamp} [{level}] {source}: {entry['message']}"
                         + (f" | {entry['detail']}" if entry["detail"] else "") + "\n")
        except OSError:
            pass  # file logging is best-effort; the ring buffer is the source of truth
    except Exception:
        pass


def recent(limit: int = 100) -> list[dict]:
    """Newest first."""
    with _LOCK:
        items = list(_RING)
    return items[::-1][: max(1, limit)]
