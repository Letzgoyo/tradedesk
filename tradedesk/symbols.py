"""Turn what a trader types ("DML", "denison") into a symbol Yahoo understands ("DML.TO")."""
from __future__ import annotations

import json
from typing import Callable

from . import config

# Common cases where the bare ticker is not what Yahoo uses. First entry is preferred.
ALIASES = {
    "DML": ["DML.TO", "DNN"],
    "NXE": ["NXE", "NXE.TO"],
    "URNM": ["URNM"],
}
SUFFIXES = [".TO", ".AX", ".V", ".L", ".NE", ".CN"]
CURRENCY = {".TO": "CAD", ".V": "CAD", ".NE": "CAD", ".CN": "CAD", ".AX": "AUD", ".L": "GBp"}


def currency_for(symbol: str) -> str:
    for suf, cur in CURRENCY.items():
        if symbol.upper().endswith(suf):
            return cur
    return "USD"


def search_yahoo(query: str, limit: int = 8) -> list[tuple[str, str]]:
    try:
        import yfinance as yf
        quotes = yf.Search(query, max_results=limit).quotes or []
    except Exception:
        return []
    keep = {"EQUITY", "ETF", "INDEX", "FUTURE", "MUTUALFUND", "CRYPTOCURRENCY"}
    return [(q["symbol"], q.get("shortname") or q.get("longname") or "") for q in quotes
            if q.get("symbol") and q.get("quoteType") in keep]


def candidates(query: str, search: Callable[[str], list] = search_yahoo) -> list[str]:
    q = query.strip().upper()
    out = [q] + ALIASES.get(q, [])
    out += [s for s, _ in search(q)]
    if "." not in q and not q.startswith("^") and "=" not in q:
        out += [q + s for s in SUFFIXES]
    elif "." in q and "." + q.rsplit(".", 1)[1] in SUFFIXES:  # wrong venue suffix: same ticker, other venues
        base = q.rsplit(".", 1)[0]
        out += [base + s for s in SUFFIXES]
    seen: list[str] = []
    for s in out:
        if s and s not in seen:
            seen.append(s)
    return seen


def _map_path():
    return config.DATA_DIR / "symbol_map.json"


def remembered(query: str) -> str | None:
    try:
        return json.loads(_map_path().read_text()).get(query.strip().upper())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def remember(query: str, symbol: str) -> None:
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            m = json.loads(_map_path().read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            m = {}
        m[query.strip().upper()] = symbol
        _map_path().write_text(json.dumps(m, indent=1))
    except OSError:
        pass


def resolve(query: str, probe: Callable[[str], object], search: Callable[[str], list] = search_yahoo):
    """probe(symbol) must return the price frame or raise. Returns (symbol, frame, note)."""
    q = query.strip().upper()
    tried: list[str] = []
    order = candidates(q, search)
    mapped = remembered(q)
    if mapped:
        order = [mapped] + [c for c in order if c != mapped]
    for sym in order:
        try:
            df = probe(sym)
        except Exception:
            tried.append(sym)
            continue
        note = "" if sym == q else f"'{q}' resolved to {sym}"
        others = [c for c in ALIASES.get(q, []) if c != sym]
        if others:
            note += f" (other listings: {', '.join(others)})"
        if sym != q:
            remember(q, sym)
        return sym, df, note
    raise LookupError(f"No price data found for '{query}'. Tried: {', '.join(tried[:10])}. "
                      "Try the full Yahoo symbol, e.g. DML.TO (TSX), BHP.AX (ASX), BP.L (London).")
