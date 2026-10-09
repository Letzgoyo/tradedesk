"""Glue: prices + technicals (daily/weekly/monthly) + market and theme regimes + drivers + micro + plan."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import config, data, events, macro, news, plan as plan_mod, profiles, symbols, technicals as ta
from .portfolio import Position

DAILY_BARS = 756  # ~3 years of daily bars for the daily read; weekly/monthly use the full history


@dataclass
class Analysis:
    ticker: str
    query: str
    df: pd.DataFrame
    dfw: pd.DataFrame | None
    tech: ta.TechReport
    htf: dict
    macro: dict
    theme: dict
    theme_how: str
    drivers: list
    rel_strength: dict
    micro: dict
    news: dict
    events: list
    plan: dict
    source: str
    note: str = ""
    currency: str = "USD"
    stale: bool = False
    name: str = ""

    def facts(self) -> dict:
        """Everything the briefing is allowed to talk about, JSON-safe."""
        def htf_facts(r):
            if r is None:
                return None
            f = r.to_facts()
            for k in ("recent_swings", "volatility", "timeframe"):
                f.pop(k, None)
            return f
        return {
            "ticker": self.ticker, "company_name": self.name, "listing_note": self.note, "currency": self.currency,
            "data_source": self.source, "data_is_stale": self.stale,
            "theme": {"name": self.theme.get("theme"), "how_detected": self.theme_how,
                      "regime": self.theme.get("regime"), "components": self.theme.get("components"),
                      "instruments": self.theme.get("instruments")},
            "driver_correlations_90d": self.drivers,
            "technicals_daily": self.tech.to_facts(),
            "technicals_weekly": htf_facts(self.htf.get("W")),
            "technicals_monthly": htf_facts(self.htf.get("M")),
            "relative_strength": self.rel_strength,
            "market_regime": {k: v for k, v in self.macro.items() if k != "sectors"},
            "upcoming_events_verify_dates": self.events,
            "micro": self.micro, "news": self.news, "rule_based_plan": self.plan,
        }


def htf_reports(df_daily: pd.DataFrame) -> tuple[dict, pd.DataFrame | None]:
    """Weekly and monthly reads. These carry the major levels the chartists you follow draw."""
    out: dict = {"W": None, "M": None}
    dfw = None
    try:
        dfw = data.resample(df_daily, "W")
        if len(dfw) >= 60:
            out["W"] = ta.analyze(dfw, "W")
        dfm = data.resample(df_daily, "M")
        if len(dfm) >= 36:
            out["M"] = ta.analyze(dfm, "M")
    except (data.DataError, ValueError, IndexError):
        pass
    return out, dfw


def relative_strength(df: pd.DataFrame, theme: str, asof=None) -> dict:
    """3-month performance against the S&P and against the benchmark that matters for this theme
    (sector ETF for a miner, Nasdaq for tech). The score uses the theme benchmark."""
    out: dict = {"score": 0, "why": "benchmark unavailable"}
    t = profiles.THEMES.get(theme, profiles.THEMES["broad"])
    r3 = lambda d: float(d["Close"].iloc[-1] / d["Close"].iloc[-64] - 1) * 100 if len(d) > 64 else None
    s3 = r3(df)
    spy = macro.frame("SPY", asof)
    primary_sym = t["etfs"][0]
    primary = macro.frame(primary_sym, asof)
    if s3 is None or (spy is None and primary is None):
        return out
    out["stock_3m"] = round(s3, 1)
    why = f"3-month {s3:+.1f}%"
    bench = None
    if spy is not None and r3(spy) is not None:
        out["spy_3m"] = round(r3(spy), 1)
        out["vs_spy_3m"] = round(s3 - r3(spy), 1)
        why += f" vs S&P {r3(spy):+.1f}%"
        bench = ("S&P", r3(spy))
    if primary is not None and r3(primary) is not None and primary_sym != "SPY":
        out["theme_etf"] = primary_sym
        out["theme_etf_3m"] = round(r3(primary), 1)
        out["vs_theme_etf_3m"] = round(s3 - r3(primary), 1)
        why += f"; {primary_sym} {r3(primary):+.1f}%"
        bench = (primary_sym, r3(primary))
    metal = t.get("metal")
    mf = macro.frame(metal, asof) if metal else None
    if mf is not None and r3(mf) is not None:
        out["metal_3m"] = round(r3(mf), 1)
        why += f"; {metal} {r3(mf):+.1f}%"
    diff = s3 - (bench[1] if bench else 0.0)
    out["score"] = 2 if diff > 10 else 1 if diff > 3 else -2 if diff < -10 else -1 if diff < -3 else 0
    out["benchmark"] = bench[0] if bench else None
    out["why"] = why + (f" (leading {bench[0]})" if out["score"] > 0 else f" (lagging {bench[0]})" if out["score"] < 0 else "")
    return out


def driver_correlations(df: pd.DataFrame, theme: str, asof=None, window: int = 90) -> list[dict]:
    """Which instrument does this stock actually move with? 90-day correlation and beta of daily returns."""
    rets = df["Close"].pct_change().dropna()
    rows = []
    seen = set()
    for sym, role in [("SPY", "S&P 500")] + profiles.instruments(theme):
        if sym in seen:
            continue
        seen.add(sym)
        f = macro.frame(sym, asof)
        if f is None:
            continue
        both = pd.concat([rets, f["Close"].pct_change()], axis=1, join="inner").dropna().tail(window)
        if len(both) < 30:
            continue
        a, b = both.iloc[:, 0], both.iloc[:, 1]
        var = b.var()
        rows.append({"symbol": sym, "role": role, "corr": round(float(a.corr(b)), 2),
                     "beta": round(float(a.cov(b) / var), 2) if var else None})
    return sorted(rows, key=lambda r: -abs(r["corr"]))


def analyze_ticker(query: str, mac: dict, pos: Position | None = None, with_micro: bool = True,
                   theme_override: str = "auto") -> Analysis:
    symbol, full, note = data.find_prices(query)
    pos = pos or Position(symbol)
    df = full.iloc[-DAILY_BARS:]
    tech = ta.analyze(df)
    htf, dfw = htf_reports(full)
    micro = data.get_micro(symbol) if with_micro else {"available": False, "news": []}
    theme_name, how = profiles.detect_theme(symbol, micro, theme_override or getattr(pos, "theme", "auto"))
    th = macro.theme_regime(theme_name)
    rs = relative_strength(df, theme_name)
    drv = driver_correlations(df, theme_name)
    name = (micro.get("fundamentals") or {}).get("shortName") or profiles.base_symbol(symbol)
    nws = news.for_stock(name, theme_name) if with_micro else {"company": [], "theme": []}
    pl = plan_mod.build_plan(tech, mac, rs, pos, theme=th, htf=htf)
    return Analysis(symbol, query.strip().upper(), df, dfw, tech, htf, mac, th, how, drv, rs, micro, nws,
                    events.upcoming(21), pl, full.attrs.get("source", "unknown"), note,
                    symbols.currency_for(symbol), bool(full.attrs.get("stale")),
                    (micro.get("fundamentals") or {}).get("shortName", ""))
