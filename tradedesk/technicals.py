"""Deterministic technical analysis: swings -> S/R zones -> Fib, trend, momentum, patterns.

Everything here is computed from OHLCV so levels are exact and repeatable. Judgment calls
(which leg matters, how much weight a pattern gets) are explicit parameters, not hidden."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Per-timeframe settings: swing sensitivity, MA lengths (fast, mid, slow), MA-slope window, Fib look-back,
# bars in a "year", and the bar counts used for the 1-bar / ~1-month / ~3-month changes.
TF_PARAMS = {
    "D": dict(atr_mult=2.5, min_pct=0.025, mas=(20, 50, 200), slope_n=20, fib_lookback=200, year=252, chg=(1, 21, 63)),
    "W": dict(atr_mult=2.5, min_pct=0.07, mas=(10, 30, 40), slope_n=8, fib_lookback=150, year=52, chg=(1, 4, 13)),
    "M": dict(atr_mult=2.0, min_pct=0.12, mas=(6, 12, 24), slope_n=4, fib_lookback=60, year=12, chg=(1, 3, 6)),
}
TF_NAMES = {"D": "daily", "W": "weekly", "M": "monthly"}
FIB_RETRACE = (0.236, 0.382, 0.5, 0.618, 0.786)
FIB_EXTEND = (1.272, 1.618)


# ---------------------------------------------------------------- indicators
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    au = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ad = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + au / ad.replace(0, np.nan))
    return out.where(ad != 0, 100.0)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=1).mean()


def macd(close: pd.Series):
    line = ema(close, 12) - ema(close, 26)
    sig = ema(line, 9)
    return line, sig, line - sig


# ---------------------------------------------------------------- swings
@dataclass
class Swing:
    i: int
    date: str
    price: float
    kind: str  # "H" or "L"
    confirmed: bool = True


def find_swings(df: pd.DataFrame, atr_mult: float = 2.5, min_pct: float = 0.025) -> list[Swing]:
    """ZigZag: a swing is confirmed once price reverses by max(atr_mult*ATR, min_pct*price).
    The final swing is returned unconfirmed (the leg price is currently making)."""
    hi, lo, cl = df["High"].values, df["Low"].values, df["Close"].values
    a = atr(df).values
    dates = df.index
    n = len(df)
    out: list[Swing] = []

    def add(i, kind, confirmed=True):
        out.append(Swing(i, str(dates[i].date()), float(hi[i] if kind == "H" else lo[i]), kind, confirmed))

    hi_i = lo_i = 0
    direction = 0
    ext = 0
    for i in range(1, n):
        thr = max(atr_mult * a[i], min_pct * cl[i])
        if direction == 0:
            if hi[i] > hi[hi_i]:
                hi_i = i
            if lo[i] < lo[lo_i]:
                lo_i = i
            if hi[i] - lo[lo_i] >= thr and i > lo_i:
                add(lo_i, "L"); direction, ext = 1, i
            elif hi[hi_i] - lo[i] >= thr and i > hi_i:
                add(hi_i, "H"); direction, ext = -1, i
            continue
        if direction == 1:
            if hi[i] > hi[ext]:
                ext = i
            elif hi[ext] - lo[i] >= thr:
                add(ext, "H"); direction, ext = -1, i
        else:
            if lo[i] < lo[ext]:
                ext = i
            elif hi[i] - lo[ext] >= thr:
                add(ext, "L"); direction, ext = 1, i
    if direction != 0:
        add(ext, "H" if direction == 1 else "L", confirmed=False)
    return out


# ---------------------------------------------------------------- support / resistance
@dataclass
class Zone:
    low: float
    high: float
    center: float
    touches: int
    strength: float
    kind: str  # "support" | "resistance" | "at"
    flip: bool  # has acted as both support and resistance
    dist_pct: float  # (center - price) / price


def sr_zones(df: pd.DataFrame, swings: list[Swing], atr_now: float, max_each: int = 4) -> list[Zone]:
    price = float(df["Close"].iloc[-1])
    n = len(df)
    tol = max(0.75 * atr_now, 0.008 * price)
    pts = sorted(swings, key=lambda s: s.price)
    clusters: list[list[Swing]] = []
    for s in pts:
        if clusters and s.price - np.mean([x.price for x in clusters[-1]]) <= tol:
            clusters[-1].append(s)
        else:
            clusters.append([s])
    zones: list[Zone] = []
    for c in clusters:
        prices = [x.price for x in c]
        center = float(np.mean(prices))
        flip = {x.kind for x in c} == {"H", "L"}
        strength = sum(0.5 + 0.5 * x.i / n for x in c) * (1.25 if flip else 1.0)
        pad = 0.15 * atr_now
        low, high = min(prices) - pad, max(prices) + pad
        kind = "at" if low <= price <= high else ("support" if center < price else "resistance")
        zones.append(Zone(low, high, center, len(c), float(strength), kind, flip, (center - price) / price))
    zones = [z for z in zones if abs(z.dist_pct) <= 0.40]  # ignore levels too far away to matter
    sup = sorted([z for z in zones if z.kind == "support"], key=lambda z: -z.strength)[:max_each]
    res = sorted([z for z in zones if z.kind == "resistance"], key=lambda z: -z.strength)[:max_each]
    at = [z for z in zones if z.kind == "at"]
    return sorted(sup + res + at, key=lambda z: z.center)


# ---------------------------------------------------------------- fibonacci
def pick_fib_leg(swings: list[Swing], n: int, lookback: int = 200):
    legs = [(a, b) for a, b in zip(swings[:-1], swings[1:]) if b.i >= n - lookback]
    if not legs:
        return None
    return max(legs[-4:], key=lambda ab: abs(ab[1].price - ab[0].price) / ab[0].price)


def fib_levels(swings: list[Swing], price: float, n: int, zones: list[Zone], lookback: int = 200) -> dict | None:
    leg = pick_fib_leg(swings, n, lookback)
    if not leg:
        return None
    a, b = leg
    up = b.price > a.price
    rng = abs(b.price - a.price)
    if up:  # pullback levels measured down from the high
        retr = {r: b.price - r * rng for r in FIB_RETRACE}
        ext = {r: a.price + r * rng for r in FIB_EXTEND}
        frac = (b.price - price) / rng
    else:  # bounce levels measured up from the low
        retr = {r: b.price + r * rng for r in FIB_RETRACE}
        ext = {r: max(a.price - r * rng, 0.01) for r in FIB_EXTEND}
        frac = (price - b.price) / rng
    tol = max(0.01 * price, 0.0)
    conf = {}
    for r, lvl in retr.items():
        hits = [z for z in zones if z.low - tol <= lvl <= z.high + tol]
        if hits:
            conf[r] = [round(z.center, 2) for z in hits]
    nearest = min(retr, key=lambda r: abs(retr[r] - price))
    return {
        "direction": "up-leg (low to high)" if up else "down-leg (high to low)",
        "up": up,
        "start": {"date": a.date, "price": round(a.price, 2)},
        "end": {"date": b.date, "price": round(b.price, 2), "provisional": not b.confirmed},
        "retracements": {str(r): round(v, 2) for r, v in retr.items()},
        "extensions": {str(r): round(v, 2) for r, v in ext.items()},
        "current_retrace": round(float(frac), 3),
        "nearest_level": {"ratio": nearest, "price": round(retr[nearest], 2)},
        "confluence_with_zones": {str(k): v for k, v in conf.items()},
    }


# ---------------------------------------------------------------- trend / patterns
def trend_state(df: pd.DataFrame, swings: list[Swing], mas=(20, 50, 200), slope_n: int = 20) -> dict:
    c = df["Close"]
    price = float(c.iloc[-1])
    s20, s50, s200 = sma(c, mas[0]), sma(c, mas[1]), sma(c, mas[2])
    conf = [s for s in swings if s.confirmed]
    highs = [s for s in conf if s.kind == "H"][-2:]
    lows = [s for s in conf if s.kind == "L"][-2:]
    structure, sscore = "mixed", 0
    if len(highs) == 2 and len(lows) == 2:
        hh, hl = highs[1].price > highs[0].price, lows[1].price > lows[0].price
        lh, ll = highs[1].price < highs[0].price, lows[1].price < lows[0].price
        if hh and hl:
            structure, sscore = "higher highs & higher lows", 2
        elif lh and ll:
            structure, sscore = "lower highs & lower lows", -2
        elif hl:
            structure, sscore = "higher lows (highs flat/lower)", 1
        elif lh:
            structure, sscore = "lower highs (lows flat/higher)", -1
    mscore = 0
    notes = []
    for name, s in ((f"{mas[1]}-bar", s50), (f"{mas[2]}-bar", s200)):
        if pd.notna(s.iloc[-1]):
            above = price > s.iloc[-1]
            mscore += 1 if above else -1
            notes.append(f"price {'above' if above else 'below'} {name} MA")
    if pd.notna(s200.iloc[-1]) and pd.notna(s50.iloc[-1]):
        mscore += 1 if s50.iloc[-1] > s200.iloc[-1] else -1
        notes.append(f"{mas[1]}MA " + ("above" if s50.iloc[-1] > s200.iloc[-1] else "below") + f" {mas[2]}MA")
    if pd.notna(s50.iloc[-1]) and len(s50.dropna()) > slope_n:
        rising = s50.iloc[-1] > s50.dropna().iloc[-slope_n]
        mscore += 1 if rising else -1
        notes.append(f"{mas[1]}MA " + ("rising" if rising else "falling"))
    score = int(np.clip(sscore + mscore, -5, 5))
    label = ("Strong uptrend" if score >= 4 else "Uptrend" if score >= 2 else "Weak uptrend / base" if score == 1
             else "Range / transition" if score == 0 else "Weak downtrend" if score == -1
             else "Downtrend" if score > -4 else "Strong downtrend")
    return {"label": label, "score": score, "structure": structure, "notes": notes,
            "sma20": _r(s20.iloc[-1]), "sma50": _r(s50.iloc[-1]), "sma200": _r(s200.iloc[-1])}


def divergence(df: pd.DataFrame, swings: list[Swing], rsi_s: pd.Series) -> str | None:
    conf = [s for s in swings if s.confirmed]
    h = [s for s in conf if s.kind == "H"][-2:]
    l = [s for s in conf if s.kind == "L"][-2:]
    n = len(df)
    if len(h) == 2 and h[1].i > n - 90 and h[1].price > h[0].price and rsi_s.iloc[h[1].i] < rsi_s.iloc[h[0].i] - 2:
        return "bearish: price made a higher high but RSI made a lower high"
    if len(l) == 2 and l[1].i > n - 90 and l[1].price < l[0].price and rsi_s.iloc[l[1].i] > rsi_s.iloc[l[0].i] + 2:
        return "bullish: price made a lower low but RSI made a higher low"
    return None


def detect_patterns(df: pd.DataFrame, swings: list[Swing], zones: list[Zone], relvol: float) -> list[str]:
    out: list[str] = []
    price = float(df["Close"].iloc[-1])
    conf = [s for s in swings if s.confirmed]
    h = [s for s in conf if s.kind == "H"][-2:]
    l = [s for s in conf if s.kind == "L"][-2:]
    n = len(df)
    if len(h) == 2 and h[1].i > n - 120 and abs(h[1].price / h[0].price - 1) < 0.015 and h[1].i - h[0].i >= 10:
        valley = df["Low"].iloc[h[0].i:h[1].i + 1].min()
        if valley < min(h[0].price, h[1].price) * 0.95:
            state = "confirmed (broke neckline)" if price < valley else "forming (neckline not broken)"
            out.append(f"double top near {h[1].price:.2f}, neckline {valley:.2f}: {state}")
    if len(l) == 2 and l[1].i > n - 120 and abs(l[1].price / l[0].price - 1) < 0.015 and l[1].i - l[0].i >= 10:
        peak = df["High"].iloc[l[0].i:l[1].i + 1].max()
        if peak > max(l[0].price, l[1].price) * 1.05:
            state = "confirmed (broke neckline)" if price > peak else "forming (neckline not broken)"
            out.append(f"double bottom near {l[1].price:.2f}, neckline {peak:.2f}: {state}")
    recent = df["Close"].iloc[-4:-1]
    for z in zones:
        if z.kind == "at":
            continue
        if z.kind == "support" and price < z.low and recent.max() >= z.low:
            out.append(f"breakdown below support {z.center:.2f}" + (" on heavy volume" if relvol >= 1.3 else " on light volume"))
        if z.kind == "resistance" and price > z.high and recent.min() <= z.high:
            out.append(f"breakout above resistance {z.center:.2f}" + (" on heavy volume" if relvol >= 1.3 else " on light volume"))
    # zones price has just moved through are re-labelled by kind, so also report 'at zone' context
    for z in zones:
        if z.kind == "at":
            out.append(f"price is inside the {z.center:.2f} zone ({z.low:.2f}-{z.high:.2f}), tested {z.touches}x")
    return out


# ---------------------------------------------------------------- volatility
def vol_profile(df: pd.DataFrame) -> dict:
    """How jumpy is this stock? Stops, 'stretched' tests and sizing advice scale with this."""
    price = float(df["Close"].iloc[-1])
    atr_pct = 100 * float(atr(df).iloc[-1]) / price
    lr = np.log(df["Close"]).diff().dropna().tail(60)
    ann = float(lr.std() * np.sqrt(252) * 100) if len(lr) > 10 else 0.0
    cls = "low" if atr_pct < 1.5 else "normal" if atr_pct < 3 else "high" if atr_pct < 5 else "extreme"
    return {"class": cls, "atr_pct": round(atr_pct, 2), "ann_vol_pct": round(ann, 1),
            "typical_month_move_pct": round(float(ann / np.sqrt(12)), 1)}


# ---------------------------------------------------------------- report
def _r(x, d=2):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), d)


@dataclass
class TechReport:
    price: float
    asof: str
    atr: float
    trend: dict
    rsi: float
    macd_state: str
    relvol: float
    extension_atr: float
    high_52w: float
    low_52w: float
    pct_from_high: float
    swings: list[Swing]
    zones: list[Zone]
    fib: dict | None
    patterns: list[str]
    divergence: str | None
    chg_1d: float
    chg_1m: float
    chg_3m: float
    tf: str = "D"
    vol: dict | None = None
    series: dict = field(default_factory=dict, repr=False)

    @property
    def supports(self) -> list[Zone]:
        return sorted([z for z in self.zones if z.kind == "support"], key=lambda z: -z.center)

    @property
    def resistances(self) -> list[Zone]:
        return sorted([z for z in self.zones if z.kind == "resistance"], key=lambda z: z.center)

    def to_facts(self) -> dict:
        z = lambda zz: {"zone": [round(zz.low, 2), round(zz.high, 2)], "center": round(zz.center, 2),
                        "touches": zz.touches, "flip_zone": zz.flip, "dist_pct": round(100 * zz.dist_pct, 1)}
        return {
            "timeframe": TF_NAMES.get(self.tf, self.tf), "volatility": self.vol,
            "price": round(self.price, 2), "asof": self.asof, "atr_14": round(self.atr, 2),
            "atr_pct": round(100 * self.atr / self.price, 2),
            "change_pct": {"1d": self.chg_1d, "1m": self.chg_1m, "3m": self.chg_3m},
            "trend": self.trend, "rsi_14": round(self.rsi, 1), "macd": self.macd_state,
            "relative_volume_vs_20d": round(self.relvol, 2),
            "extension_above_20ema_in_atr": round(self.extension_atr, 2),
            "52w_high": round(self.high_52w, 2), "52w_low": round(self.low_52w, 2),
            "pct_below_52w_high": round(self.pct_from_high, 1),
            "support_zones": [z(s) for s in self.supports],
            "resistance_zones": [z(r) for r in self.resistances],
            "inside_zone": [z(a) for a in self.zones if a.kind == "at"],
            "fibonacci": self.fib, "patterns": self.patterns, "rsi_divergence": self.divergence,
            "recent_swings": [{"date": s.date, "price": round(s.price, 2), "type": "high" if s.kind == "H" else "low",
                               "confirmed": s.confirmed} for s in self.swings[-6:]],
        }


def analyze(df: pd.DataFrame, tf: str = "D") -> TechReport:
    P = TF_PARAMS[tf]
    c = df["Close"]
    price = float(c.iloc[-1])
    a_s = atr(df)
    a = float(a_s.iloc[-1])
    swings = find_swings(df, P["atr_mult"], P["min_pct"])
    zones = sr_zones(df, [s for s in swings if s.confirmed], a)  # untested provisional extreme is not a level
    n = len(df)
    r_s = rsi(c)
    line, sig, hist = macd(c)
    if line.iloc[-1] > sig.iloc[-1]:
        macd_state = "bullish (MACD above signal, histogram " + ("rising)" if hist.iloc[-1] > hist.iloc[-2] else "falling)")
    else:
        macd_state = "bearish (MACD below signal, histogram " + ("rising)" if hist.iloc[-1] > hist.iloc[-2] else "falling)")
    vol20 = df["Volume"].rolling(20).mean().iloc[-1]
    relvol = float(df["Volume"].iloc[-1] / vol20) if vol20 and vol20 > 0 else 1.0
    yr = df.iloc[-P["year"]:]
    hi52, lo52 = float(yr["High"].max()), float(yr["Low"].min())
    b1, bm, bq = P["chg"]
    chg = lambda k: round(100 * (price / float(c.iloc[-1 - k]) - 1), 2) if n > k else 0.0
    ext = (price - float(ema(c, P["mas"][0]).iloc[-1])) / a if a else 0.0
    return TechReport(
        price=price, asof=str(df.index[-1].date()), atr=a,
        trend=trend_state(df, swings, P["mas"], P["slope_n"]), rsi=float(r_s.iloc[-1]),
        macd_state=macd_state, relvol=relvol, extension_atr=float(ext), high_52w=hi52, low_52w=lo52,
        pct_from_high=100 * (price / hi52 - 1), swings=swings, zones=zones,
        fib=fib_levels(swings, price, n, zones, P["fib_lookback"]), patterns=detect_patterns(df, swings, zones, relvol),
        divergence=divergence(df, swings, r_s), chg_1d=chg(b1), chg_1m=chg(bm), chg_3m=chg(bq), tf=tf,
        vol=vol_profile(df) if tf == "D" else None,
        series={"sma20": sma(c, P["mas"][0]), "sma50": sma(c, P["mas"][1]), "sma200": sma(c, P["mas"][2]), "rsi": r_s},
    )
