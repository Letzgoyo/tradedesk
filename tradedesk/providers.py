"""Price providers. Yahoo is primary (covers TSX/ASX/LSE and futures). Stooq (free) and Polygon
(optional key, US only) are fallbacks. Each returns a raw OHLCV DataFrame or raises."""
from __future__ import annotations

import io
import os
import time
import urllib.request

import pandas as pd

UA = {"User-Agent": "Mozilla/5.0 (TradeDesk personal tool)"}


def _http_get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # honours HTTPS_PROXY from the environment
        return r.read().decode("utf-8", "replace")


# ------------------------------------------------------------------ Yahoo
def yahoo(ticker: str, period: str = "10y") -> pd.DataFrame:
    import yfinance as yf
    last: Exception | None = None
    for attempt in range(3):
        try:
            raw = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
            if raw is not None and not raw.empty:
                return raw
            last = RuntimeError("empty response")
        except Exception as exc:
            last = exc
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"yahoo: {last}")


# ------------------------------------------------------------------ Stooq (free, no key)
_STOOQ_SUFFIX = {"": ".us", ".L": ".uk", ".DE": ".de", ".HK": ".hk", ".T": ".jp"}


def stooq_symbol(ticker: str) -> str | None:
    t = ticker.strip()
    if t.startswith("^") or "=" in t or "-" in t:
        return None
    base, dot, ext = t.partition(".")
    suffix = _STOOQ_SUFFIX.get(("." + ext.upper()) if dot else "")
    return (base.lower() + suffix) if suffix else None


def parse_stooq_csv(text: str) -> pd.DataFrame:
    if not text.strip() or text.lstrip().lower().startswith(("no data", "<", "exceeded")):
        raise RuntimeError("stooq: no data")
    df = pd.read_csv(io.StringIO(text), parse_dates=["Date"], index_col="Date")
    if df.empty or "Close" not in df.columns:
        raise RuntimeError("stooq: unexpected format")
    return df


def stooq(ticker: str, period: str = "10y") -> pd.DataFrame:
    sym = stooq_symbol(ticker)
    if not sym:
        raise RuntimeError("stooq: unsupported symbol")
    return parse_stooq_csv(_http_get(f"https://stooq.com/q/d/l/?s={sym}&i=d"))


# ------------------------------------------------------------------ Polygon (optional)
def parse_polygon(payload: dict) -> pd.DataFrame:
    rows = payload.get("results") or []
    if not rows:
        raise RuntimeError(f"polygon: no results ({payload.get('status')})")
    df = pd.DataFrame(rows).rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    df.index = pd.to_datetime(df["t"], unit="ms")
    return df[["Open", "High", "Low", "Close", "Volume"]]


def polygon(ticker: str, period: str = "10y") -> pd.DataFrame:
    import json
    key = os.getenv("POLYGON_API_KEY")
    if not key:
        raise RuntimeError("polygon: no POLYGON_API_KEY")
    if "." in ticker or ticker.startswith("^") or "=" in ticker:
        raise RuntimeError("polygon: US tickers only")
    years = int(period.rstrip("y")) if period.endswith("y") and period[:-1].isdigit() else 5
    end = pd.Timestamp.today().normalize()
    start = end - pd.DateOffset(years=years)
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker.upper()}/range/1/day/{start:%Y-%m-%d}/{end:%Y-%m-%d}"
           f"?adjusted=true&sort=asc&limit=50000&apiKey={key}")
    return parse_polygon(json.loads(_http_get(url)))


def chain():
    """(name, fn) in the order they are tried."""
    return [("yahoo", yahoo), ("polygon", polygon), ("stooq", stooq)]
