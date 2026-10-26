"""LLM client — OpenAI-compatible endpoint, JSON-disciplined.

Credentials in data/secrets/llm_api.json (gitignored):
    {"base_url": "https://...", "api_key": "...", "model": "..."}
The LLM is an ADVISOR: its output is parsed, validated, and can only act
within the deterministic risk envelope. Failures degrade to 'no opinion'
and never block the trading loop.
"""
from __future__ import annotations

import json
import re

import httpx

from .config import DATA_DIR

SECRETS = DATA_DIR / "secrets" / "llm_api.json"


def load_config() -> dict | None:
    if SECRETS.exists():
        cfg = json.loads(SECRETS.read_text())
        if cfg.get("base_url") and cfg.get("api_key") and cfg.get("model"):
            return cfg
    return None


def chat(system: str, user: str, temperature: float = 0.2, max_tokens: int = 800,
         json_mode: bool = True, timeout: float = 90.0) -> str:
    """One chat completion; raises LLMError on hard failure after retries."""
    cfg = load_config()
    if not cfg:
        raise LLMError("llm_api.json not configured")
    body: dict = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    url = cfg["base_url"].rstrip("/") + "/v1/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['api_key']}",
               "Content-Type": "application/json"}
    last = ""
    for attempt in range(3):
        try:
            r = httpx.post(url, json=body, headers=headers, timeout=timeout)
            if r.status_code == 200:
                choice = r.json()["choices"][0]
                content = choice["message"]["content"]
                if content and content.strip():
                    return content
                if choice.get("finish_reason") == "length":
                    body["max_tokens"] = min(4000, int(body.get("max_tokens", 800)) * 2)
                    last = f"length-capped, retrying with {body['max_tokens']}"
                    continue
                last = "empty content"
            else:
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                # some proxies reject response_format; retry without it
                if r.status_code == 400 and "response_format" in body:
                    body.pop("response_format")
                    continue
        except httpx.HTTPError as e:
            last = f"transport: {e}"
    raise LLMError(last)


def parse_json_loose(text: str) -> dict:
    """Parse JSON possibly wrapped in markdown fences or prose."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise LLMError("unparseable LLM response")


class LLMError(RuntimeError):
    pass
