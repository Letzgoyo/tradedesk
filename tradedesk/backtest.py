"""Walk-forward backtest of the hold/trim/exit stance.

At each decision date (every `step` bars) the plan is rebuilt from ONLY the data up to that date:
prices, market regime, theme regime, weekly/monthly structure. Then we measure what happened next
and what a simple rule that follows the stance would have done versus just holding.

This answers 'does the stance add anything on this stock?', not 'will it work next year'.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import analysis, data, macro, plan as plan_mod, profiles, technicals as ta
from .portfolio import Position

# Share of the position held after each stance (what the plan is telling you to do).
EXPOSURE = {
    "HOLD": 1.0, "HOLD, ADD ON PULLBACK": 1.0,
    "TRIM INTO STRENGTH": 0.66, "HOLD, SELL INTO THE BOUNCE": 0.66,
    "TRIM / SELL AND REBUY LOWER": 0.33, "REDUCE": 0.33,
    "EXIT / REDUCE HARD": 0.0,
}
HOLDISH = ("HOLD", "HOLD, ADD ON PULLBACK")
CUTISH = ("TRIM / SELL AND REBUY LOWER", "REDUCE", "EXIT / REDUCE HARD")


def stance_at(full: pd.DataFrame, i: int, ticker: str, theme_name: str, horizon: str = "position") -> dict:
    """The plan as it would have been at bar i, using nothing after bar i."""
    sub = full.iloc[: i + 1]
    asof = sub.index[-1]
    daily = sub.iloc[-analysis.DAILY_BARS:]
    mac = macro.macro_regime(asof)
    th = macro.theme_regime(theme_name, asof)
    tech = ta.analyze(daily)
    htf, _ = analysis.htf_reports(sub)
    rs = analysis.relative_strength(daily, theme_name, asof)
    pl = plan_mod.build_plan(tech, mac, rs, Position(ticker, 1, 0, horizon), theme=th, htf=htf)
    return {"stance": pl["stance"], "score": pl["score"], "max": pl["max_score"], "stop": pl["levels"]["stop"]}


def _stats(ret: pd.Series, exposure: pd.Series | None = None) -> dict:
    eq = (1 + ret).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    return {"total_return_pct": round(100 * float(eq.iloc[-1] - 1), 1), "max_drawdown_pct": round(100 * float(dd), 1),
            "avg_exposure_pct": round(100 * float(exposure.mean())) if exposure is not None else 100}


def run(query: str, theme: str = "auto", years: float = 5, step: int = 5, warmup: int = 300,
        horizons=(20, 60), cost_bps: float = 10.0, progress=None) -> dict:
    symbol, full, note = data.find_prices(query)
    theme_name, _ = profiles.detect_theme(symbol, None, theme)
    n = len(full)
    start = max(warmup, n - int(years * 252))
    idxs = list(range(start, n - 1, step))
    if len(idxs) < 10:
        raise data.DataError(f"Not enough history to backtest {symbol} ({n} bars).")

    rows = []
    for k, i in enumerate(idxs):
        r = stance_at(full, i, symbol, theme_name)
        r.update(i=i, date=full.index[i], price=float(full["Close"].iloc[i]))
        rows.append(r)
        if progress:
            progress((k + 1) / len(idxs))
    close, low = full["Close"].values, full["Low"].values
    for r in rows:
        i = r["i"]
        for h in horizons:
            j = i + h
            r[f"fwd{h}"] = float(close[j] / close[i] - 1) if j < n else np.nan
            r[f"mae{h}"] = float(low[i + 1: j + 1].min() / close[i] - 1) if j < n else np.nan
    dec = pd.DataFrame(rows)

    # ---- strategy: follow the stance, no lookahead (decision at close of bar i applies from bar i+1)
    exp = np.ones(n)
    for k, r in enumerate(rows):
        end = rows[k + 1]["i"] if k + 1 < len(rows) else n - 1
        exp[r["i"] + 1: end + 1] = EXPOSURE.get(r["stance"], 1.0)
    ret = pd.Series(close[1:] / close[:-1] - 1, index=full.index[1:])
    s0 = rows[0]["i"] + 1
    e = pd.Series(exp[1:], index=full.index[1:])
    turnover = e.diff().abs().fillna(0)
    strat = (e * ret - turnover * cost_bps / 1e4).iloc[s0 - 1:]
    bh = ret.iloc[s0 - 1:]
    expo = e.iloc[s0 - 1:]
    equity = pd.DataFrame({"Follow the stance": (1 + strat).cumprod(), "Buy and hold": (1 + bh).cumprod(),
                           "Exposure": expo})

    # ---- by-stance outcomes
    f60 = f"fwd{horizons[-1]}"
    fwd_cols = [f"fwd{h}" for h in horizons]
    g = dec.groupby("stance").agg(signals=("stance", "size"), **{c: (c, "mean") for c in fwd_cols},
                                  hit_rate=(f60, lambda s: float((s.dropna() > 0).mean()) if s.notna().any() else np.nan),
                                  worst_dip=(f"mae{horizons[0]}", "mean"))
    base = dec[fwd_cols + [f60]].mean(numeric_only=True)
    buckets = g.reset_index().sort_values("signals", ascending=False)

    # ---- in-sample vs out-of-sample halves (60/40 by time)
    cut = rows[int(len(rows) * 0.6)]["i"]
    seg = lambda a, b: {"strategy": _stats(strat[(strat.index > full.index[a]) & (strat.index <= full.index[b])],
                                           expo[(expo.index > full.index[a]) & (expo.index <= full.index[b])]),
                        "buy_hold": _stats(bh[(bh.index > full.index[a]) & (bh.index <= full.index[b])])}
    segments = {"first_60pct": seg(rows[0]["i"], cut), "last_40pct": seg(cut, n - 1)}

    summary = {"strategy": _stats(strat, expo), "buy_hold": _stats(bh), "decisions": len(rows),
               "stance_changes": int((dec["stance"] != dec["stance"].shift()).sum() - 1),
               "years": round((n - 1 - rows[0]["i"]) / 252, 1)}
    return {"symbol": symbol, "theme": theme_name, "note": note, "summary": summary, "buckets": buckets,
            "baseline": base.to_dict(), "equity": equity, "segments": segments, "decisions": dec,
            "verdict": verdict(summary, buckets, base, segments, f60)}


def verdict(summary: dict, buckets: pd.DataFrame, base: pd.Series, segments: dict, f60: str) -> list[str]:
    s, b = summary["strategy"], summary["buy_hold"]
    out = []
    dret, ddd = s["total_return_pct"] - b["total_return_pct"], s["max_drawdown_pct"] - b["max_drawdown_pct"]
    if ddd > 3 and dret >= -5:
        out.append(f"Helpful: following the stance cut the worst drawdown from {b['max_drawdown_pct']}% to {s['max_drawdown_pct']}% "
                   f"with return {s['total_return_pct']}% vs {b['total_return_pct']}% buy-and-hold.")
    elif ddd > 3:
        out.append(f"Trade-off: drawdown improved ({b['max_drawdown_pct']}% to {s['max_drawdown_pct']}%) but you gave up "
                   f"{abs(dret):.0f} points of return ({s['total_return_pct']}% vs {b['total_return_pct']}%).")
    elif dret > 5:
        out.append(f"Beat buy-and-hold by {dret:.0f} points ({s['total_return_pct']}% vs {b['total_return_pct']}%), "
                   "but without a meaningfully smaller drawdown.")
    else:
        out.append(f"No clear edge here: {s['total_return_pct']}% vs {b['total_return_pct']}% buy-and-hold, "
                   f"worst drawdown {s['max_drawdown_pct']}% vs {b['max_drawdown_pct']}%.")
    hold = buckets[buckets["stance"].isin(HOLDISH)]
    cut = buckets[buckets["stance"].isin(CUTISH)]
    if len(cut) and cut["signals"].sum() >= 8 and len(hold) and hold["signals"].sum() >= 8:
        wh = np.average(hold[f60].fillna(0), weights=hold["signals"])
        wc = np.average(cut[f60].fillna(0), weights=cut["signals"])
        if wh > wc:
            out.append(f"Signal quality: after HOLD stances the next ~3 months averaged {100 * wh:+.1f}%, after TRIM/EXIT stances "
                       f"{100 * wc:+.1f}%. The warnings pointed the right way on average.")
        else:
            out.append(f"Signal quality is poor on this stock: after TRIM/EXIT stances the next ~3 months averaged {100 * wc:+.1f}% "
                       f"versus {100 * wh:+.1f}% after HOLD. It was selling into strength.")
    else:
        out.append("Too few HOLD or TRIM/EXIT signals to judge signal quality on this stock.")
    a, z = segments["first_60pct"], segments["last_40pct"]
    d1 = a["strategy"]["total_return_pct"] - a["buy_hold"]["total_return_pct"]
    d2 = z["strategy"]["total_return_pct"] - z["buy_hold"]["total_return_pct"]
    if (d1 > 0) != (d2 > 0):
        out.append("Unstable: the stance beat buy-and-hold in one half of the period but not the other. Don't lean on this result.")
    out.append(f"Caution: {summary['decisions']} decision points over {summary['years']} years on one stock is a small sample. "
               "No trading costs beyond 0.1% per change, no tax, and the result is for this stock's history only. "
               "Run it on every holding before reading anything into it.")
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--years", type=float, default=5)
    ap.add_argument("--theme", default="auto")
    a = ap.parse_args()
    res = run(a.ticker, a.theme, a.years)
    print(f"\n{res['symbol']} ({res['theme']}) {res['summary']['years']}y, {res['summary']['decisions']} decisions")
    print("follow stance:", res["summary"]["strategy"], "\nbuy & hold:   ", res["summary"]["buy_hold"])
    print("\n", res["buckets"].round(3).to_string(index=False))
    print("\n".join("- " + v for v in res["verdict"]))
