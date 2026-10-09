"""Market-regime read: trend, volatility, rates, dollar, credit, breadth, sector rotation."""
from __future__ import annotations

import pandas as pd

from . import config, data, technicals as ta

SECTORS = ["XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLU", "XLRE", "XLB", "XLC"]
SECTOR_NAMES = {"XLK": "Technology", "XLF": "Financials", "XLV": "Health Care", "XLY": "Consumer Discretionary",
                "XLP": "Consumer Staples", "XLE": "Energy", "XLI": "Industrials", "XLU": "Utilities",
                "XLRE": "Real Estate", "XLB": "Materials", "XLC": "Communication Services"}


def _try(sym: str):
    try:
        return data.get_prices(sym, "1y")
    except data.DataError:
        return None


def _ret(df: pd.DataFrame, k: int) -> float:
    return round(100 * (float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-1 - k]) - 1), 2) if len(df) > k else 0.0


def macro_regime() -> dict:
    parts: list[dict] = []
    nums: dict = {}

    def add(name: str, score: int, why: str):
        parts.append({"factor": name, "score": score, "why": why})

    spy = _try("SPY")
    if spy is None:
        return {"available": False, "label": "Unknown", "score": 0, "score_norm": 0, "components": [],
                "note": "Could not load market data."}
    c = spy["Close"]
    p = float(c.iloc[-1])
    s50, s200 = ta.sma(c, 50).iloc[-1], ta.sma(c, 200).iloc[-1]
    nums["SPY"] = round(p, 2)
    if pd.notna(s200):
        add("S&P 500 vs 200-day", 1 if p > s200 else -1, f"SPY {'above' if p > s200 else 'below'} its 200-day ({s200:.2f})")
    if pd.notna(s50):
        add("S&P 500 vs 50-day", 1 if p > s50 else -1, f"SPY {'above' if p > s50 else 'below'} its 50-day ({s50:.2f})")
    add("S&P 500 momentum", 1 if _ret(spy, 21) > 0 else -1, f"1-month return {_ret(spy, 21):+.1f}%")

    vix = _try("^VIX")
    if vix is not None:
        v = float(vix["Close"].iloc[-1]); v10 = float(vix["Close"].iloc[-11]) if len(vix) > 11 else v
        sc = 1 if v < 16 else 0 if v < 22 else -1 if v < 30 else -2
        if v10 and v / v10 > 1.2:
            sc -= 1
        nums["VIX"] = round(v, 2)
        add("Volatility (VIX)", sc, f"VIX {v:.1f} ({'+' if v >= v10 else ''}{100 * (v / v10 - 1):.0f}% vs 10 days ago)")

    tnx = _try("^TNX")
    if tnx is not None:
        y = float(tnx["Close"].iloc[-1]); y21 = float(tnx["Close"].iloc[-22]) if len(tnx) > 22 else y
        d = y - y21
        nums["US10Y"] = round(y, 2)
        add("10-year yield", -1 if d > 0.25 else 0, f"10y at {y:.2f}% ({d:+.2f} pts over 1 month)"
            + ("; rising fast pressures valuations" if d > 0.25 else ""))

    dxy = _try("DX-Y.NYB")
    if dxy is not None:
        r = _ret(dxy, 21)
        nums["DXY"] = round(float(dxy["Close"].iloc[-1]), 2)
        add("US dollar", -1 if r > 2 else 1 if r < -2 else 0, f"DXY {r:+.1f}% over 1 month")

    hyg, ief = _try("HYG"), _try("IEF")
    if hyg is not None and ief is not None:
        ratio = (hyg["Close"] / ief["Close"]).dropna()
        m50 = ratio.rolling(50).mean().iloc[-1]
        if pd.notna(m50):
            ok = ratio.iloc[-1] > m50
            add("Credit risk appetite", 1 if ok else -1,
                f"high-yield vs treasuries ratio {'above' if ok else 'below'} its 50-day (credit {'confirming' if ok else 'warning'})")

    sect = {}
    for t in SECTORS:
        d = _try(t)
        if d is not None:
            m = ta.sma(d["Close"], 50).iloc[-1]
            sect[t] = {"name": SECTOR_NAMES[t], "1m": _ret(d, 21), "3m": _ret(d, 63),
                       "above_50d": bool(pd.notna(m) and d["Close"].iloc[-1] > m)}
    if sect:
        share = sum(v["above_50d"] for v in sect.values()) / len(sect)
        add("Breadth (sectors above 50-day)", 1 if share > 0.65 else -1 if share < 0.35 else 0,
            f"{round(100 * share)}% of sector ETFs above their 50-day")

    total = sum(p_["score"] for p_ in parts)
    norm = max(-2, min(2, round(total / 3.0)))
    label = {2: "Risk-on", 1: "Constructive", 0: "Mixed / neutral", -1: "Cautious", -2: "Risk-off"}[norm]
    ranked = sorted(sect.items(), key=lambda kv: kv[1]["1m"], reverse=True)
    return {"available": True, "label": label, "score": total, "score_norm": norm, "components": parts,
            "levels": nums, "sectors": sect,
            "leaders_1m": [f"{k} {v['name']} {v['1m']:+.1f}%" for k, v in ranked[:3]],
            "laggards_1m": [f"{k} {v['name']} {v['1m']:+.1f}%" for k, v in ranked[-3:]],
            "asof": str(spy.index[-1].date()),
            "note": "Check the economic calendar (FOMC, CPI, jobs) yourself: scheduled events are not in this feed."}
