"""Rule-based, position-aware plan. Transparent on purpose: every stance is the sum of named
factors you can read, argue with and tune. Claude later explains it, it doesn't replace it."""
from __future__ import annotations

from .portfolio import Position
from .technicals import TechReport

# Share of the position to act on when trimming, by holding style.
TRIM_FRACTION = {"swing": "one-third to one-half", "position": "one-quarter to one-third", "long_term": "10-25%"}

# Tunable knobs (the backtest page lets you see how they behave; change them deliberately, not to fit noise).
PARAMS = {
    "stop_buffer_atr": {"low": 0.5, "normal": 0.5, "high": 1.0, "extreme": 1.5},   # beyond the support level
    "min_stop_atr": {"low": 1.5, "normal": 1.5, "high": 2.0, "extreme": 2.5},      # never closer than this
    "strong_pct": 0.33,   # score / max-possible above this: healthy, hold or add
    "weak_pct": -0.22,    # below this: trim or sell
    "meaningful_target_atr": 1.5,
}
RESOURCE = {"uranium", "gold", "silver", "copper", "energy", "lithium"}


def _dedupe(levels: list[dict], tol: float = 0.012) -> list[dict]:
    out: list[dict] = []
    for lv in levels:
        dup = next((o for o in out if abs(lv["price"] / o["price"] - 1) < tol), None)
        if dup is None:
            out.append(lv)
        elif lv.get("major") and not dup.get("major"):
            out[out.index(dup)] = lv  # prefer the higher-timeframe label
    return out


def _htf_score(htf: dict | None) -> tuple[int, str]:
    parts = []
    for key, name in (("W", "weekly"), ("M", "monthly")):
        r = (htf or {}).get(key)
        if r is not None:
            parts.append((name, r.trend["score"], r.trend["label"]))
    if not parts:
        return 0, "higher-timeframe data not available"
    avg = sum(p[1] for p in parts) / len(parts)
    s = 2 if avg >= 3 else 1 if avg >= 1 else 0 if avg > -1 else -1 if avg > -3 else -2
    return s, "; ".join(f"{n}: {lbl}" for n, _, lbl in parts)


def build_plan(tech: TechReport, macro: dict, rel_strength: dict, pos: Position | None,
               theme: dict | None = None, htf: dict | None = None) -> dict:
    price, a = tech.price, tech.atr
    tr = tech.trend
    held = bool(pos and pos.held)
    horizon = pos.horizon if pos else "position"
    vol = tech.vol or {"class": "normal", "atr_pct": round(100 * a / price, 2), "ann_vol_pct": 0.0, "typical_month_move_pct": 0.0}
    vc = vol["class"]
    theme_name = (theme or {}).get("theme", "broad")
    is_resource = theme_name in RESOURCE and (theme or {}).get("available")
    w, m = (htf or {}).get("W"), (htf or {}).get("M")

    # ---- levels: daily zones, weekly/monthly (major) zones, Fib, moving averages
    sup_levels: list[dict] = []
    res_levels: list[dict] = []

    def zone_levels(rep: TechReport | None, label: str, major: bool):
        if rep is None:
            return
        for z in rep.zones:
            if z.kind not in ("support", "resistance"):
                continue
            item = {"price": round(z.center, 2), "major": major,
                    "why": f"{label} {z.kind} zone ({z.touches} touches{', flipped' if z.flip else ''})"}
            (sup_levels if z.center < price else res_levels).append(item)

    zone_levels(tech, "daily", False)
    zone_levels(w, "weekly", True)
    zone_levels(m, "monthly", True)
    for name, v in (("50-day MA", tr["sma50"]), ("200-day MA", tr["sma200"])):
        if v:
            (sup_levels if v < price else res_levels).append(
                {"price": round(v, 2), "major": name.startswith("200"), "why": name + (" (support)" if v < price else " (resistance)")})

    def fib_into(rep: TechReport | None, label: str, major: bool):
        if rep is None or not rep.fib:
            return
        f = rep.fib
        for r, v in f["retracements"].items():
            if float(r) < 0.382:
                continue
            item = {"price": v, "major": major, "why": f"{label} Fib {r} {'pullback' if f['up'] else 'bounce'} level"}
            if f["up"] and v < price:
                sup_levels.append(item)
            elif not f["up"] and v > price:
                res_levels.append(item)
        if not f["up"] or label == "daily":
            for r, v in f["extensions"].items():
                if v > price:
                    res_levels.append({"price": v, "major": major, "why": f"{label} Fib {r} extension target"})

    fib_into(tech, "daily", False)
    fib_into(w, "weekly", True)
    if tech.high_52w > price * 1.005:
        res_levels.append({"price": round(tech.high_52w, 2), "major": True, "why": "52-week high"})
    sup_levels = _dedupe(sorted(sup_levels, key=lambda x: -x["price"]))
    res_levels = _dedupe(sorted(res_levels, key=lambda x: x["price"]))

    below = [s for s in sup_levels if s["price"] < price - 0.5 * a]
    stop_base = below[0]["price"] if below else price - 3 * a
    stop = stop_base - PARAMS["stop_buffer_atr"][vc] * a
    stop = round(min(stop, price - PARAMS["min_stop_atr"][vc] * a), 2)
    risk = max(price - stop, 0.01)
    meaningful = [r for r in res_levels if r["price"] >= price + PARAMS["meaningful_target_atr"] * a]
    t1 = meaningful[0]["price"] if meaningful else price + 2 * a
    rr = round((t1 - price) / risk, 2)

    # ---- scoring
    f: list[dict] = []

    def add(name: str, s: int, why: str, mx: int = 2):
        f.append({"factor": name, "score": s, "max": mx, "why": why})

    ts = tr["score"]
    add("Trend (daily)", 2 if ts >= 3 else 1 if ts >= 1 else 0 if ts == 0 else -1 if ts >= -2 else -2,
        f"{tr['label']}; {tr['structure']}")
    hs, hwhy = _htf_score(htf)
    add("Trend (weekly/monthly)", hs, hwhy)
    mom, why = 0, []
    if "bullish" in tech.macd_state:
        mom += 1; why.append("MACD bullish")
    else:
        mom -= 1; why.append("MACD bearish")
    if tech.rsi > 75:
        mom -= 1; why.append(f"RSI {tech.rsi:.0f} overbought")
    elif tech.rsi < 30:
        mom += 1; why.append(f"RSI {tech.rsi:.0f} oversold (bounce potential)")
    if tech.divergence:
        mom += 1 if tech.divergence.startswith("bullish") else -1
        why.append(tech.divergence.split(":")[0] + " RSI divergence")
    add("Momentum", max(-2, min(2, mom)), "; ".join(why))
    mk = macro.get("score_norm", 0)
    if is_resource:  # a miner follows its metal first, the S&P second
        add("Market backdrop", int(mk / 2), f"S&P regime: {macro.get('label', 'unknown')} (half weight for a resource stock)", 1)
        add(f"{(theme or {}).get('label', 'Theme')} backdrop", theme.get("score_norm", 0),
            f"{theme.get('regime', 'unknown')}: " + "; ".join(c["why"] for c in theme.get("components", [])[:2]))
    else:
        add("Market backdrop", mk, f"market regime: {macro.get('label', 'unknown')}")
    add("Relative strength", rel_strength.get("score", 0), rel_strength.get("why", "n/a"))
    loc, wloc = 0, []
    if rr >= 2:
        loc += 1; wloc.append(f"reward/risk {rr}:1 to first resistance")
    elif rr < 1:
        loc -= 1; wloc.append(f"poor reward/risk {rr}:1 to first resistance")
    ext_pct = max(0.12, 6 * vol["atr_pct"] / 100)  # how far above the 50-day counts as stretched scales with volatility
    extended = tech.extension_atr > 3.0 or bool(tr["sma50"] and price > tr["sma50"] * (1 + ext_pct))
    if extended:
        loc -= 1; wloc.append("stretched far above its moving averages")
    add("Location / risk-reward", loc, "; ".join(wloc) or f"reward/risk {rr}:1", 1)
    total = sum(x["score"] for x in f)
    max_total = sum(x["max"] for x in f)
    pct = total / max_total

    below200 = bool(tr["sma200"] and price < tr["sma200"])
    weekly_down = bool(w and w.trend["score"] <= -3)
    confirmed_down = (below200 and ts <= -2) or (weekly_down and ts <= -1)
    broke_support = any("breakdown" in p for p in tech.patterns)
    oversold = tech.rsi < 32 or tech.extension_atr < -2.5
    strong, weak = pct >= PARAMS["strong_pct"], pct <= PARAMS["weak_pct"]

    pnl = None
    if held and pos.cost_basis > 0:
        pnl = {"pct": round(100 * (price / pos.cost_basis - 1), 1), "value": round(price * pos.shares, 2),
               "gain": round((price - pos.cost_basis) * pos.shares, 2)}
    big_gain = bool(pnl and pnl["pct"] >= 40)

    if held:
        if confirmed_down or (broke_support and pct < 0):
            stance = "EXIT / REDUCE HARD" if horizon != "long_term" else "REDUCE"
            headline = "Trend is broken. Protect capital, then look to rebuy at lower support or after a reclaim."
        elif weak and oversold:
            stance = "HOLD, SELL INTO THE BOUNCE"
            headline = "Weak setup, but already oversold. Don't sell the low; use a bounce to reduce."
        elif weak:
            stance = "TRIM / SELL AND REBUY LOWER"
            headline = "Setup has deteriorated. Take some off and wait for a level to rebuild."
        elif (extended or rr < 1 or (big_gain and pct < 0.12)) and not oversold:
            stance = "TRIM INTO STRENGTH"
            headline = "Trend is intact but risk/reward is stretched. Bank part, keep the rest."
        elif strong and rr >= 1.5:
            stance = "HOLD, ADD ON PULLBACK"
            headline = "Healthy setup. Hold, and add only at the pullback levels below."
        else:
            stance = "HOLD"
            headline = "No strong reason to act. Hold and let the levels below decide."
    else:
        if strong and rr >= 2 and not extended:
            stance = "BUY / START POSITION"
            headline = "Trend, backdrop and reward/risk line up. Starter position is reasonable."
        elif pct >= 0.11:
            stance = "WAIT FOR PULLBACK"
            headline = "Decent stock, poor entry. Wait for the pullback levels."
        else:
            stance = "AVOID / WAIT"
            headline = "Setup not favourable. Wait for the trend to repair."

    # ---- if/then ladder
    rules: list[str] = []
    buy_zone = [s for s in sup_levels if s["price"] < price - 0.5 * a][:3]
    if held:
        if buy_zone:
            rules.append(f"If price pulls back to {buy_zone[0]['price']} ({buy_zone[0]['why']}) and holds, that is where adding makes sense.")
        rules.append(f"If price closes below {stop}, the setup is invalidated: reduce or exit ({TRIM_FRACTION[horizon]} first, rest on a failed bounce).")
        if res_levels:
            rules.append(f"If price reaches {res_levels[0]['price']} ({res_levels[0]['why']}), consider trimming {TRIM_FRACTION[horizon]}.")
        if len(res_levels) > 1:
            rules.append(f"If it closes above {res_levels[0]['price']} on strong volume, the next target is {res_levels[1]['price']}.")
        if stance.startswith("HOLD, SELL") and res_levels:
            rules.append(f"Bounce plan: if price recovers to {res_levels[0]['price']} ({res_levels[0]['why']}) and stalls, reduce {TRIM_FRACTION[horizon]}.")
        if stance.startswith(("TRIM", "EXIT", "REDUCE")) and buy_zone:
            rules.append("Rebuy plan: scale back in at " + ", ".join(str(b["price"]) for b in buy_zone) + " once price stabilises (a higher low or reclaim of the 20-day).")
    else:
        if buy_zone:
            rules.append("Entry zones: " + ", ".join(f"{b['price']} ({b['why']})" for b in buy_zone) + ".")
        rules.append(f"Invalidation: a close below {stop}.")
        if res_levels:
            rules.append(f"First target {res_levels[0]['price']}; reward/risk from here {rr}:1.")
    dts, wts = ts, (w.trend["score"] if w else 0)
    if w and dts >= 1 and wts <= -2:
        rules.append("Higher-timeframe warning: the daily bounce is inside a weekly downtrend. Rallies into weekly resistance are more likely to fail than break.")
    elif w and dts <= -1 and wts >= 2:
        rules.append("Higher-timeframe context: this daily pullback sits inside a weekly uptrend, which historically is the better place to add than to sell.")
    if tech.extension_atr < -2:
        rules.append("Price is stretched below its 20-day average; bounces are sharp but unreliable in a downtrend.")
    if vc in ("high", "extreme"):
        rules.append(f"{vc.capitalize()}-volatility stock (about {vol['atr_pct']}% a day, roughly +/-{vol['typical_month_move_pct']}% in a typical month): "
                     f"the stop is {round(100 * (price - stop) / price, 1)}% below price on purpose so normal noise doesn't shake you out. Keep the position smaller than you would for a calmer stock.")

    alerts = [{"price": stop, "direction": "below", "why": "invalidation / stop"}]
    alerts += [{"price": b["price"], "direction": "below", "why": "pullback level: " + b["why"]} for b in buy_zone[:2]]
    alerts += [{"price": r["price"], "direction": "above", "why": "target / trim: " + r["why"]} for r in res_levels[:2]]
    for al in alerts:
        al["dist_pct"] = round(100 * (al["price"] / price - 1), 1)

    return {
        "stance": stance, "headline": headline, "score": total, "max_score": max_total, "factors": f,
        "levels": {"stop": stop, "support": sup_levels[:6], "resistance": res_levels[:6], "reward_risk": rr},
        "position": {"shares": pos.shares, "cost_basis": pos.cost_basis, "horizon": horizon, "pnl": pnl} if held else None,
        "theme": {"name": theme_name, "regime": (theme or {}).get("regime"), "score_norm": (theme or {}).get("score_norm")},
        "htf": {k: {"trend": r.trend["label"], "score": r.trend["score"]} for k, r in (("weekly", w), ("monthly", m)) if r is not None},
        "volatility": vol, "rules": rules, "alerts": alerts,
        "caveat": "Rule-based heuristics, not a forecast or personalised financial/tax advice.",
    }
