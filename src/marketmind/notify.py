"""Telegram notifier.

Credentials live in data/secrets/telegram.json (gitignored):
    {"bot_token": "...", "chat_id": "..."}
or environment variables TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.
Never hardcode or commit secrets. Sends via api.telegram.org; if unreachable
(e.g. Iranian IP without proxy), set {"base_url": "https://<proxy-host>/bot"}.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from .config import DATA_DIR

SECRETS = DATA_DIR / "secrets" / "telegram.json"


def load_config() -> dict | None:
    if SECRETS.exists():
        return json.loads(SECRETS.read_text())
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        return {"bot_token": token, "chat_id": chat}
    return None


def send(message: str) -> bool:
    cfg = load_config()
    if not cfg:
        return False  # not configured: silent no-op
    base = cfg.get("base_url", "https://api.telegram.org")
    url = f"{base}/bot{cfg['bot_token']}/sendMessage"
    try:
        r = httpx.post(url, json={"chat_id": cfg["chat_id"], "text": message},
                       timeout=15)
        return r.status_code == 200
    except httpx.HTTPError:
        return False
