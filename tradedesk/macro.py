"""Market regime plus theme regimes (gold, silver, copper, uranium, ...). Every function accepts
`asof` so the backtest can replay history without peeking at the future."""
from __future__ import annotations

import pandas as pd

from . import data, profiles, technicals as ta

SECTORS = ["XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLU", "XLRE", "XLB", "XLC"]
SECTOR_NAMES = {"XLK": "Technology", "XLF": "Financials", "XLV": "Health Care", "XLY": "Consumer Discretionary",
                "XLP": "Consumer Staples", "XLE": "Energy", "XLI": "Industrials", "XLU": "Utilities",
                "XLRE": "Real Estate", "XLB": "Materials", "XLC": "Communication Services"}
WINDOW = 300  # bars of history each indicator needs at most


def frame(sym: str, asof=None):
    """Last WINDOW daily bars up to `asof`, or None if the symbol has no usable data."""
    try:
        df = data.get_prices(sym, "10y")
    except data.DataError:
        return None
    if asof is not None:
        df = df.loc[:pd.Timestamp(asof)]
    df = df.iloc[-WINDOW:]
    return df if len(df) > 60 else None


def _ret(df: pd.DataFrame, k: int) -> float:
    return round(100 * (float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-1 - k]) - 1), 2) if len(df) > k else 0.0


def _norm(total: float, scale: float) -> int:
    return int(max(-2, min(2, round(total / scale))))


# ------------------------------------------------------------------ whole-market regime
def macro_regime(asof=None) -> dict:
    parts: list[dict] = []
    nums: dict = {}

    def add(name: str, score: int, why: str):
        parts.append({"factor": name, "score": score, "why": why})

    spy = frame("SPY", asof)
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

    vix = frame("^VIX", asof)
    if vix is not None:
        v = float(vix["Close"].iloc[-1]); v10 = float(vix["Close"].iloc[-11]) if len(vix) > 11 else v
        sc = 1 if v < 16 else 0 if v < 22 else -1 if v < 30 else -2
        if v10 and v / v10 > 1.2:
            sc -= 1
        nums["VIX"] = round(v, 2)
        add("Volatility (VIX)", sc, f"VIX {v:.1f} ({'+' if v >= v10 else ''}{100 * (v / v10 - 1):.0f}% vs 10 days ago)")

    tnx = frame("^TNX", asof)
    if tnx is not None:
        y = float(tnx["Close"].iloc[-1]); y21 = float(tnx["Close"].iloc[-22]) if len(tnx) > 22 else y
        d = y - y21
        nums["US10Y"] = round(y, 2)
        add("10-year yield", -1 if d > 0.25 else 0, f"10y at {y:.2f}% ({d:+.2f} pts over 1 month)"
            + ("; rising fast pressures valuations" if d > 0.25 else ""))

    dxy = frame("DX-Y.NYB", asof)
    if dxy is not None:
        r = _ret(dxy, 21)
        nums["DXY"] = round(float(dxy["Close"].iloc[-1]), 2)
        add("US dollar", -1 if r > 2 else 1 if r < -2 else 0, f"DXY {r:+.1f}% over 1 month")

    hyg, ief = frame("HYG", asof), frame("IEF", asof)
    if hyg is not None and ief is not None:
        ratio = (hyg["Close"] / ief["Close"]).dropna()
        m50 = ratio.rolling(50).mean().iloc[-1]
        if pd.notna(m50):
            ok = ratio.iloc[-1] > m50
            add("Credit risk appetite", 1 if ok else -1,
                f"high-yield vs treasuries ratio {'above' if ok else 'below'} its 50-day (credit {'confirming' if ok else 'warning'})")

    sect = {}
    for t in SECTORS:
        d = frame(t, asof)
        if d is not None:
            m = ta.sma(d["Close"], 50).iloc[-1]
            sect[t] = {"name": SECTOR_NAMES[t], "1m": _ret(d, 21), "3m": _ret(d, 63),
                       "above_50d": bool(pd.notna(m) and d["Close"].iloc[-1] > m)}
    if sect:
        share = sum(v["above_50d"] for v in sect.values()) / len(sect)
        add("Breadth (sectors above 50-day)", 1 if share > 0.65 else -1 if share < 0.35 else 0,
            f"{round(100 * share)}% of sector ETFs above their 50-day")

    total = sum(p_["score"] for p_ in parts)
    norm = _norm(total, 3.0)
    label = {2: "Risk-on", 1: "Constructive", 0: "Mixed / neutral", -1: "Cautious", -2: "Risk-off"}[norm]
    ranked = sorted(sect.items(), key=lambda kv: kv[1]["1m"], reverse=True)
    return {"available": True, "label": label, "score": total, "score_norm": norm, "components": parts,
            "levels": nums, "sectors": sect,
            "leaders_1m": [f"{k} {v['name']} {v['1m']:+.1f}%" for k, v in ranked[:3]],
            "laggards_1m": [f"{k} {v['name']} {v['1m']:+.1f}%" for k, v in ranked[-3:]],
            "asof": str(spy.index[-1].date())}


# ------------------------------------------------------------------ theme regimes
def _instrument_row(sym: str, role: str, df: pd.DataFrame) -> dict:
    c = df["Close"]
    m50, m200 = ta.sma(c, 50).iloc[-1], ta.sma(c, 200).iloc[-1]
    price = float(c.iloc[-1])
    return {"symbol": sym, "role": role, "price": round(price, 2), "1m": _ret(df, 21), "3m": _ret(df, 63),
            "above_50d": bool(pd.notna(m50) and price > m50), "above_200d": bool(pd.notna(m200) and price > m200)}


def theme_regime(theme: str, asof=None) -> dict:
    """Is the tide helping this stock's own world? Metal trend, sector-ETF trend, whether miners
    lead the metal, and the dollar/yield headwinds for precious metals."""
    t = profiles.THEMES.get(theme, profiles.THEMES["broad"])
    rows: dict[str, dict] = {}
    frames: dict[str, pd.DataFrame] = {}
    for sym, role in profiles.instruments(theme):
        df = frame(sym, asof)
        if df is not None:
            frames[sym] = df
            rows[sym] = _instrument_row(sym, role, df)
    out = {"theme": theme, "label": t["label"], "instruments": list(rows.values()), "components": [], "available": bool(rows)}
    if not rows:
        return {**out, "score": 0, "score_norm": 0, "regime": "unknown"}
    parts = out["components"]

    def add(name, score, why):
        parts.append({"factor": name, "score": score, "why": why})

    metal = t.get("metal")
    if metal in rows:
        r = rows[metal]
        add(f"{t['label']} price trend", (1 if r["above_200d"] else -1) + (1 if r["above_50d"] else -1),
            f"{t['metal_label']}: {'above' if r['above_200d'] else 'below'} 200-day, {'above' if r['above_50d'] else 'below'} 50-day, 3m {r['3m']:+.1f}%")
    etfs = [rows[s] for s in t["etfs"] if s in rows]
    if etfs:
        sc = sum((1 if e["above_200d"] else -1) + (1 if e["above_50d"] else -1) for e in etfs) / len(etfs)
        add("Sector ETF trend", int(round(sc)), ", ".join(f"{e['symbol']} {e['3m']:+.1f}% (3m)" for e in etfs))
    if metal in rows and etfs:
        lead = etfs[0]["3m"] - rows[metal]["3m"]
        add("Equities vs metal", 1 if lead > 5 else -1 if lead < -5 else 0,
            f"{etfs[0]['symbol']} {etfs[0]['3m']:+.1f}% vs metal {rows[metal]['3m']:+.1f}% over 3m "
            + ("(equities leading: market is paying up for the metal)" if lead > 5 else
               "(equities lagging the metal: sceptical)" if lead < -5 else "(in step)"))
    for s in t.get("inverse", []):
        if s in rows:
            move = rows[s]["1m"]
            name = {"DX-Y.NYB": "US dollar", "^TNX": "10y yield"}.get(s, s)
            add(f"{name} (headwind if rising)", -1 if move > 2 else 1 if move < -2 else 0, f"{name} {move:+.1f}% over 1 month")
    leader = t.get("leader")
    if leader in rows:
        add("Bellwether", 1 if rows[leader]["above_50d"] else -1,
            f"{leader} {'above' if rows[leader]['above_50d'] else 'below'} its 50-day, 3m {rows[leader]['3m']:+.1f}%")
    total = sum(p["score"] for p in parts)
    norm = _norm(total, 2.0)
    out.update(score=total, score_norm=norm,
               regime={2: "Strong tailwind", 1: "Tailwind", 0: "Neutral", -1: "Headwind", -2: "Strong headwind"}[norm],
               asof=str(next(iter(frames.values())).index[-1].date()))
    return out


def ratios(asof=None) -> list[dict]:
    """Gold/silver and copper/gold: classic cross-metal reads."""
    out = []
    for name, a, b, up_means, down_means in (
        ("Gold/Silver ratio", "GC=F", "SI=F", "silver lagging gold (defensive)", "silver outperforming (risk appetite)"),
        ("Copper/Gold ratio", "HG=F", "GC=F", "growth/risk-on", "growth fears / defensive"),
    ):
        fa, fb = frame(a, asof), frame(b, asof)
        if fa is None or fb is None:
            continue
        r = (fa["Close"] / fb["Close"]).dropna()
        if len(r) < 60:
            continue
        m50 = r.rolling(50).mean().iloc[-1]
        chg = 100 * (r.iloc[-1] / r.iloc[-22] - 1)
        out.append({"ratio": name, "value": round(float(r.iloc[-1]), 4), "1m %": round(float(chg), 1),
                    "reads": up_means if r.iloc[-1] > m50 else down_means})
    return out


def themes_overview(asof=None) -> dict:
    return {t: theme_regime(t, asof) for t in ("gold", "silver", "copper", "uranium", "energy", "lithium")}
