"""Market data. Real data comes from yfinance; a synthetic generator exists only so the
engine can be tested where Yahoo is unreachable (enable with TRADEDESK_DEMO=1)."""
from __future__ import annotations

import time
import zlib

import numpy as np
import pandas as pd

from . import config, providers, symbols

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
    "GC=F": (2300.0, 0.009, False), "SI=F": (28.0, 0.016, False), "HG=F": (4.3, 0.012, False),
}


def _years(period: str) -> float:
    return float(period[:-1]) if period.endswith("y") and period[:-1].isdigit() else 2.0


def synthetic_prices(symbol: str, n: int = 2600) -> pd.DataFrame:
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


def _cache_file(ticker: str, period: str):
    safe = "".join(ch if ch.isalnum() else "_" for ch in ticker.upper())
    return config.DATA_DIR / "cache" / f"{safe}_{period}.csv"


def _write_disk(ticker: str, period: str, df: pd.DataFrame) -> None:
    try:
        f = _cache_file(ticker, period)
        f.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(f)
    except OSError:
        pass


def _read_disk(ticker: str, period: str) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(_cache_file(ticker, period), index_col=0, parse_dates=True)
        return _clean(df) if len(df) >= 60 else None
    except (OSError, ValueError, KeyError):
        return None


def get_prices(ticker: str, period: str = "10y") -> pd.DataFrame:
    """Daily OHLCV for an exact symbol. Tries each provider, then falls back to the last good
    on-disk copy (flagged stale) rather than failing when the network or Yahoo is down."""
    key = (ticker.upper(), period)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < config.PRICE_TTL_SECONDS:
        return hit[1]
    errors: list[str] = []
    df = None
    if config.DEMO:
        df = synthetic_prices(ticker.upper(), n=int(252 * _years(period)))
    else:
        for name, fn in providers.chain():
            try:
                raw = fn(ticker, period)
                cand = _clean(raw)
                if len(cand) < 60:
                    raise DataError(f"only {len(cand)} bars")
                cand.attrs["source"] = name
                df = cand
                _write_disk(ticker, period, df)
                break
            except Exception as exc:
                errors.append(f"{name}: {str(exc)[:80]}")
        if df is None:
            old = _read_disk(ticker, period)
            if old is not None:
                old.attrs["source"] = f"stale cache (last bar {old.index[-1].date()})"
                old.attrs["stale"] = True
                df = old
    if df is None:
        raise DataError(f"Could not load prices for {ticker} ({'; '.join(errors) or 'no providers'})")
    _cache[key] = (time.time(), df)
    return df


def find_prices(query: str, period: str = "10y"):
    """Like get_prices but accepts loose input ('DML', 'denison') and resolves the right listing.
    Returns (symbol, frame, note)."""
    if config.DEMO:
        q = query.strip().upper()
        return q, get_prices(q, period), ""
    try:
        return symbols.resolve(query, lambda sym: get_prices(sym, period))
    except LookupError as exc:
        raise DataError(str(exc)) from exc


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Daily -> weekly ('W') or monthly ('M') bars. The latest bar is the period in progress."""
    rules = {"W": ["W-FRI"], "M": ["ME", "M"]}[tf]
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    for rule in rules:
        try:
            out = df.resample(rule).agg(agg).dropna(subset=["Close"])
            out.attrs.update(df.attrs)
            return out
        except ValueError:
            continue
    raise DataError(f"cannot resample to {tf}")


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
