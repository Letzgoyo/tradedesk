"""Headlines from Google News RSS (free, no key): one query for the company, one for its theme
(e.g. 'uranium price'). Headlines only; Claude weighs them in the briefing."""
from __future__ import annotations

import html
import urllib.parse
import xml.etree.ElementTree as ET

from . import config, providers

THEME_QUERY = {
    "uranium": "uranium price OR uranium miners", "gold": "gold price", "silver": "silver price",
    "copper": "copper price", "energy": "oil price", "lithium": "lithium price",
}


def parse_rss(xml_text: str, limit: int = 8) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = html.unescape((item.findtext("title") or "").strip())
        if not title:
            continue
        src = item.findtext("source") or ""
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        out.append({"title": title, "publisher": src, "date": (item.findtext("pubDate") or "").strip(),
                    "link": (item.findtext("link") or "").strip()})
        if len(out) >= limit:
            break
    return out


def search(query: str, days: int = 14, limit: int = 8) -> list[dict]:
    if config.DEMO:
        return []
    q = urllib.parse.quote(f"{query} when:{days}d")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    try:
        return parse_rss(providers._http_get(url, timeout=10), limit)
    except Exception:
        return []


def for_stock(name_or_ticker: str, theme: str) -> dict:
    return {"company": search(f'"{name_or_ticker}" stock', limit=8),
            "theme": search(THEME_QUERY[theme], limit=6) if theme in THEME_QUERY else []}
