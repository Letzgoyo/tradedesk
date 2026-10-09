"""Push notifications to your phone. Configure any of these as Replit Secrets (env vars):

  NTFY_TOPIC            easiest: install the free ntfy app, subscribe to a long random topic name
  NTFY_SERVER           optional, default https://ntfy.sh
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
  DISCORD_WEBHOOK_URL
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


def _post(url: str, data: bytes, headers: dict | None = None, timeout: int = 15) -> None:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status >= 300:
            raise RuntimeError(f"HTTP {r.status}")


def configured() -> list[str]:
    ch = []
    if os.getenv("NTFY_TOPIC"):
        ch.append("ntfy")
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        ch.append("telegram")
    if os.getenv("DISCORD_WEBHOOK_URL"):
        ch.append("discord")
    return ch


def _ascii(s: str) -> str:
    return s.encode("ascii", "ignore").decode()


def send(title: str, body: str, urgent: bool = False) -> list[dict]:
    """Send to every configured channel. Returns one result per channel; never raises."""
    results = []
    for ch in configured():
        try:
            if ch == "ntfy":
                server = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
                _post(f"{server}/{urllib.parse.quote(os.environ['NTFY_TOPIC'])}", body.encode(),
                      {"Title": _ascii(title) or "TradeDesk", "Priority": "high" if urgent else "default"})
            elif ch == "telegram":
                tok = os.environ["TELEGRAM_BOT_TOKEN"]
                _post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json.dumps({"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": f"{title}\n\n{body}"}).encode(),
                      {"Content-Type": "application/json"})
            elif ch == "discord":
                _post(os.environ["DISCORD_WEBHOOK_URL"], json.dumps({"content": f"**{title}**\n{body}"[:1900]}).encode(),
                      {"Content-Type": "application/json"})
            results.append({"channel": ch, "ok": True})
        except Exception as exc:
            results.append({"channel": ch, "ok": False, "error": str(exc)[:120].replace(os.getenv("TELEGRAM_BOT_TOKEN") or "\0", "***")})
    return results
