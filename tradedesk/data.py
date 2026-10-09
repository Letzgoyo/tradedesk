"""Market data. Real data comes from yfinance; a synthetic generator exists only so the
engine can be tested where Yahoo is unreachable (enable with TRADEDESK_DEMO=1)."""
from __future__ import annotations

import time
import zlib

import numpy as np
import pandas as pd

from . import config

COLS = ["Open", "High", "Low", "Close", "Volume"]
_cache: dict = {}


class DataError(RuntimeError):
    pass


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df[[c for c in COLS if c in df.columns]].dropna(subset=["Close"]).copy()
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df.index = pd.to_datetime(df.index).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    for c in ("Open", "High", "Low"):
        df[c] = df[c].fillna(df["Close"])
    df["Volume"] = df.get("Volume", pd.Series(0, index=df.index)).fillna(0)
    return df


_SYN_BASE = {  # symbol: (start level, daily vol, mean-reverting?)
    "^VIX": (18.0, 0.05, True), "^TNX": (4.2, 0.012, True), "DX-Y.NYB": (102.0, 0.004, True),
    "SPY": (450.0, 0.010, False),
}


def synthetic_prices(symbol: str, n: int = 520) -> pd.DataFrame:
    rng = np.random.default_rng(zlib.crc32(symbol.encode()))
    base, vol, mean_rev = _SYN_BASE.get(symbol, (float(rng.uniform(40, 300)), float(rng.uniform(0.012, 0.022)), False))
    rets = np.zeros(n)
    i = 0
    while i < n:  # regime-switching drift gives trends, ranges and reversals
        seg = int(rng.integers(30, 110))
        drift = rng.uniform(-0.0025, 0.0035)
        rets[i:i + seg] = rng.normal(drift, vol, size=min(seg, n - i))
        i += seg
    if mean_rev:
        x = np.empty(n); x[0] = base
        for k in range(1, n):
            x[k] = x[k - 1] + 0.05 * (base - x[k - 1]) + base * vol * rng.normal()
        close = np.maximum(x, base * 0.3)
    else:
        close = base * np.exp(np.cumsum(rets))
    opn = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, vol / 4, n))
    hi = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    lo = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    volu = rng.lognormal(15, 0.35, n) * (1 + 2 * np.abs(np.r_[0, np.diff(close)] / close) / vol)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    df = pd.DataFrame({"Open": opn, "High": hi, "Low": lo, "Close": close, "Volume": volu}, index=idx)
    df.attrs["source"] = "demo"
    return df


def get_prices(ticker: str, period: str = "2y") -> pd.DataFrame:
    key = (ticker.upper(), period)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < config.PRICE_TTL_SECONDS:
        return hit[1]
    try:
        import yfinance as yf
        raw = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
        if raw is None or raw.empty:
            raise DataError(f"No price data returned for {ticker}")
        df = _clean(raw)
        df.attrs["source"] = "yfinance"
    except Exception as exc:  # network, rate limit, bad ticker
        if config.DEMO:
            df = synthetic_prices(ticker.upper())
        else:
            raise DataError(f"Could not load prices for {ticker}: {exc}") from exc
    if len(df) < 60:
        raise DataError(f"Only {len(df)} bars for {ticker}; need at least 60")
    _cache[key] = (time.time(), df)
    return df


def _get(d: dict, *keys):
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def get_micro(ticker: str) -> dict:
    """Fundamentals, earnings date and headlines. Best effort: every field may be missing."""
    out: dict = {"available": False, "news": []}
    if config.DEMO:
        return out
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
    except Exception:
        return out
    try:
        info = t.info or {}
        keep = ["shortName", "sector", "industry", "marketCap", "trailingPE", "forwardPE", "pegRatio",
                "priceToBook", "revenueGrowth", "earningsGrowth", "profitMargins", "debtToEquity",
                "beta", "dividendYield", "recommendationMean", "targetMeanPrice", "targetLowPrice",
                "targetHighPrice", "shortPercentOfFloat", "heldPercentInstitutions"]
        out["fundamentals"] = {k: info.get(k) for k in keep if info.get(k) is not None}
        out["available"] = bool(out["fundamentals"])
    except Exception:
        pass
    try:
        cal = t.calendar
        ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if ed:
            out["next_earnings"] = str(ed[0] if isinstance(ed, (list, tuple)) else ed)
    except Exception:
        pass
    try:
        for item in (t.news or [])[:8]:
            c = item.get("content", item)
            title = _get(c, "title")
            if title:
                prov = c.get("provider")
                out["news"].append({
                    "title": title,
                    "publisher": (prov.get("displayName") if isinstance(prov, dict) else _get(c, "publisher")),
                    "date": str(_get(c, "pubDate", "providerPublishTime") or ""),
                })
    except Exception:
        pass
    return out
