"""Annotated chart: candles, MAs, S/R zones, Fib levels, swings, stop/targets, volume, RSI."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .technicals import TF_PARAMS  # noqa: E402

UP, DOWN = "#1a9c6b", "#d64545"
SUP, RES, FIB, MA = "#2a7de1", "#e0852d", "#8a63d2", ("#6c7a89", "#2a7de1", "#111111")


def render(an, bars: int | None = None, show_fib: bool = True, tf: str = "D"):
    """tf='D' daily (with weekly zones outlined on top) or 'W' weekly."""
    if tf == "W" and an.dfw is not None and an.htf.get("W") is not None:
        src, tech, bars = an.dfw, an.htf["W"], bars or 156
    else:
        tf, src, tech, bars = "D", an.df, an.tech, bars or 180
    pl = an.plan
    df = src.iloc[-bars:]
    off = len(src) - len(df)
    x = np.arange(len(df))
    fig, (ax, axv, axr) = plt.subplots(3, 1, figsize=(13, 9), sharex=True,
                                       gridspec_kw={"height_ratios": [6, 1.3, 1.5], "hspace": 0.04})
    up = df["Close"] >= df["Open"]
    colors = np.where(up, UP, DOWN)
    ax.vlines(x, df["Low"], df["High"], color=colors, linewidth=0.8, zorder=2)
    ax.bar(x, (df["Close"] - df["Open"]).abs().clip(lower=df["Close"].iloc[-1] * 0.0004), bottom=df[["Open", "Close"]].min(axis=1),
           color=colors, width=0.7, zorder=3)
    lens = TF_PARAMS[tf]["mas"]
    unit = "day" if tf == "D" else "wk"
    for name, ln, col, lw in (("sma20", lens[0], MA[0], 0.9), ("sma50", lens[1], MA[1], 1.2), ("sma200", lens[2], MA[2], 1.4)):
        ax.plot(x, tech.series[name].iloc[-bars:].values, color=col, linewidth=lw, label=f"{ln}-{unit}", zorder=1)

    xr = len(df) - 1
    if tf == "D" and an.htf.get("W") is not None:  # major (weekly) levels outlined on the daily chart
        for z in an.htf["W"].zones:
            if z.kind in ("support", "resistance"):
                ax.axhspan(z.low, z.high, facecolor="none", edgecolor=SUP if z.kind == "support" else RES,
                           linewidth=1.1, linestyle="--", alpha=0.8, zorder=1)
                ax.text(len(df) * 0.02, z.center, f"W {z.center:.2f}", fontsize=7.5, va="center",
                        color=SUP if z.kind == "support" else RES)
    for z in tech.zones:
        col = SUP if z.kind == "support" else RES if z.kind == "resistance" else "#999999"
        ax.axhspan(z.low, z.high, color=col, alpha=0.13, zorder=0)
        ax.text(xr + 1.5, z.center, f"{z.center:.2f}", color=col, va="center", fontsize=8)
    if show_fib and tech.fib:
        for r, v in tech.fib["retracements"].items():
            ax.axhline(v, color=FIB, linestyle=":", linewidth=0.9, zorder=1)
            ax.text(2, v, f"Fib {r}  {v:.2f}", color=FIB, fontsize=7.5, va="bottom")
    lv = pl["levels"]
    ax.axhline(lv["stop"], color=DOWN, linestyle="--", linewidth=1.1)
    lab = dict(fontsize=8, bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=1.2))
    ax.text(len(df) * 0.30, lv["stop"], f"stop / invalidation {lv['stop']:.2f}", color=DOWN, va="top", **lab)
    for r in lv["resistance"][:2]:
        ax.axhline(r["price"], color=UP, linestyle="--", linewidth=0.9)
        ax.text(len(df) * 0.30, r["price"], f"target {r['price']:.2f}", color=UP, va="bottom", **lab)
    cost = (pl.get("position") or {}).get("cost_basis")
    lo, hi = float(df["Low"].min()), float(df["High"].max())
    if cost and lo * 0.85 <= cost <= hi * 1.15:
        ax.axhline(cost, color="#555", linestyle="-.", linewidth=1)
        ax.text(len(df) * 0.62, cost, f"your cost {cost:.2f}", color="#555", va="bottom", **lab)
    elif cost:
        ax.text(0.99, 0.02, f"your cost {cost:.2f} (off this chart)", transform=ax.transAxes, ha="right", fontsize=8, color="#555")
    # fit the view to the price action plus the plan's stop and first targets, not to far-away zones
    near = [lv["stop"]] + [r["price"] for r in lv["resistance"][:2]]
    ax.set_ylim(min([lo] + near) * 0.97, max([hi] + near) * 1.03)

    for s in tech.swings:
        j = s.i - off
        if 0 <= j < len(df):
            ax.plot(j, s.price, marker="v" if s.kind == "H" else "^", color="#222", markersize=5,
                    markerfacecolor="white" if not s.confirmed else "#222", zorder=4)

    ax.set_xlim(-1, len(df) + 12)
    ax.set_title(f"{an.ticker}   {tech.price:.2f} {an.currency}   {pl['stance']}   |   {'weekly' if tf == 'W' else 'daily'}: {tech.trend['label']}",
                 loc="left", fontsize=12)
    ax.legend(loc="upper left", fontsize=8, frameon=False, ncol=3)
    ax.grid(alpha=0.15)

    axv.bar(x, df["Volume"], color=colors, width=0.7)
    axv.set_ylabel("Vol", fontsize=8); axv.set_yticks([])
    axr.plot(x, tech.series["rsi"].iloc[-bars:].values, color="#444", linewidth=1)
    axr.axhline(70, color=DOWN, linewidth=0.6, linestyle="--"); axr.axhline(30, color=UP, linewidth=0.6, linestyle="--")
    axr.set_ylim(0, 100); axr.set_ylabel("RSI", fontsize=8)
    ticks = np.linspace(0, len(df) - 1, 7).astype(int)
    axr.set_xticks(ticks)
    axr.set_xticklabels([df.index[t].strftime("%b %d" if tf == "D" else "%b %Y") for t in ticks], fontsize=8)
    for a in (ax, axv, axr):
        for sp in ("top", "right"):
            a.spines[sp].set_visible(False)
    fig.subplots_adjust(left=0.05, right=0.93, top=0.95, bottom=0.05)
    return fig
