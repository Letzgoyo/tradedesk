"""Glue: prices + technicals + macro + relative strength + micro + plan for one ticker."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import config, data, plan as plan_mod, technicals as ta
from .portfolio import Position


@dataclass
class Analysis:
    ticker: str
    df: pd.DataFrame
    tech: ta.TechReport
    macro: dict
    rel_strength: dict
    micro: dict
    plan: dict
    source: str

    def facts(self) -> dict:
        """Everything the briefing is allowed to talk about, JSON-safe."""
        return {
            "ticker": self.ticker, "data_source": self.source,
            "technicals": self.tech.to_facts(), "relative_strength": self.rel_strength,
            "macro": {k: v for k, v in self.macro.items() if k != "sectors"},
            "micro": self.micro, "rule_based_plan": self.plan,
        }


def relative_strength(df: pd.DataFrame, sector: str | None) -> dict:
    out: dict = {"score": 0, "why": "benchmark unavailable"}
    try:
        spy = data.get_prices("SPY", "1y")
    except data.DataError:
        return out
    r = lambda d, k: float(d["Close"].iloc[-1] / d["Close"].iloc[-1 - k] - 1) * 100
    s3, m3 = r(df, 63), r(spy, 63)
    out.update({"stock_3m": round(s3, 1), "spy_3m": round(m3, 1), "vs_spy_3m": round(s3 - m3, 1)})
    why = f"3-month {s3:+.1f}% vs S&P {m3:+.1f}%"
    etf = config.SECTOR_ETF.get(sector or "")
    if etf:
        try:
            e3 = r(data.get_prices(etf, "1y"), 63)
            out.update({"sector_etf": etf, "sector_3m": round(e3, 1), "vs_sector_3m": round(s3 - e3, 1)})
            why += f"; sector {etf} {e3:+.1f}%"
        except data.DataError:
            pass
    diff = s3 - m3
    out["score"] = 2 if diff > 10 else 1 if diff > 3 else -2 if diff < -10 else -1 if diff < -3 else 0
    out["why"] = why + (" (leader)" if out["score"] > 0 else " (laggard)" if out["score"] < 0 else "")
    return out


def analyze_ticker(ticker: str, macro: dict, pos: Position | None = None, with_micro: bool = True) -> Analysis:
    ticker = ticker.strip().upper()
    df = data.get_prices(ticker, "2y")
    tech = ta.analyze(df)
    micro = data.get_micro(ticker) if with_micro else {"available": False, "news": []}
    sector = (micro.get("fundamentals") or {}).get("sector")
    rs = relative_strength(df, sector)
    pl = plan_mod.build_plan(tech, macro, rs, pos)
    return Analysis(ticker, df, tech, macro, rs, micro, pl, df.attrs.get("source", "unknown"))
