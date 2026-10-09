"""Rule-based, position-aware plan. Transparent on purpose: every stance is the sum of
named factors you can read, argue with and tune. Claude later explains it, it doesn't replace it."""
from __future__ import annotations

from .portfolio import Position
from .technicals import TechReport

# Share of the position to act on when trimming, by holding style.
TRIM_FRACTION = {"swing": "one-third to one-half", "position": "one-quarter to one-third", "long_term": "10-25%"}


def _dedupe(levels: list[dict], price: float, tol: float = 0.012) -> list[dict]:
    out: list[dict] = []
    for lv in levels:
        if not any(abs(lv["price"] / o["price"] - 1) < tol for o in out):
            out.append(lv)
    return out


def build_plan(tech: TechReport, macro: dict, rel_strength: dict, pos: Position | None) -> dict:
    price, a = tech.price, tech.atr
    tr = tech.trend
    held = bool(pos and pos.held)
    horizon = pos.horizon if pos else "position"

    # ---- levels
    sup_levels = [{"price": round(z.center, 2), "why": f"support zone ({z.touches} touches{', flipped' if z.flip else ''})"}
                  for z in tech.supports]
    for name, v in (("50-day MA", tr["sma50"]), ("200-day MA", tr["sma200"])):
        if v and v < price:
            sup_levels.append({"price": round(v, 2), "why": name})
    if tech.fib:
        for r, v in tech.fib["retracements"].items():
            if v < price and (tech.fib["up"]):
                sup_levels.append({"price": v, "why": f"Fib {r} retracement"})
    sup_levels = _dedupe(sorted(sup_levels, key=lambda x: -x["price"]), price)

    res_levels = [{"price": round(z.center, 2), "why": f"resistance zone ({z.touches} touches{', flipped' if z.flip else ''})"}
                  for z in tech.resistances]
    if tech.fib:
        for r, v in tech.fib["retracements"].items():
            if v > price and not tech.fib["up"] and float(r) >= 0.382:
                res_levels.append({"price": v, "why": f"Fib {r} bounce level"})
        for r, v in tech.fib["extensions"].items():
            if v > price:
                res_levels.append({"price": v, "why": f"Fib {r} extension target"})
    if tech.high_52w > price * 1.005:
        res_levels.append({"price": round(tech.high_52w, 2), "why": "52-week high"})
    res_levels = _dedupe(sorted(res_levels, key=lambda x: x["price"]), price)

    below = [s for s in sup_levels if s["price"] < price - 0.5 * a]
    stop_base = below[0]["price"] if below else price - 3 * a
    stop = round(stop_base - 0.5 * a, 2)
    risk = max(price - stop, 0.01)
    meaningful = [r for r in res_levels if r["price"] >= price + 1.5 * a]
    t1 = meaningful[0]["price"] if meaningful else price + 2 * a
    rr = round((t1 - price) / risk, 2)

    # ---- scoring
    f: list[dict] = []

    def add(name: str, s: int, why: str):
        f.append({"factor": name, "score": s, "why": why})

    ts = tr["score"]
    add("Trend", 2 if ts >= 3 else 1 if ts >= 1 else 0 if ts == 0 else -1 if ts >= -2 else -2,
        f"{tr['label']}; {tr['structure']}")
    mom = 0
    why = []
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
    add("Macro backdrop", macro.get("score_norm", 0), f"market regime: {macro.get('label', 'unknown')}")
    rs = rel_strength.get("score", 0)
    add("Relative strength", rs, rel_strength.get("why", "n/a"))
    loc = 0
    wloc = []
    if rr >= 2:
        loc += 1; wloc.append(f"reward/risk {rr}:1 to first resistance")
    elif rr < 1:
        loc -= 1; wloc.append(f"poor reward/risk {rr}:1 to first resistance")
    extended = tech.extension_atr > 3.0 or (tr["sma50"] and price > tr["sma50"] * 1.18)
    if extended:
        loc -= 1; wloc.append("stretched far above its moving averages")
    add("Location / risk-reward", loc, "; ".join(wloc) or f"reward/risk {rr}:1")
    total = sum(x["score"] for x in f)

    below200 = bool(tr["sma200"] and price < tr["sma200"])
    confirmed_down = below200 and ts <= -2
    broke_support = any("breakdown" in p for p in tech.patterns)

    # ---- stance
    pnl = None
    if held and pos.cost_basis > 0:
        pnl = {"pct": round(100 * (price / pos.cost_basis - 1), 1), "value": round(price * pos.shares, 2),
               "gain": round((price - pos.cost_basis) * pos.shares, 2)}
    big_gain = bool(pnl and pnl["pct"] >= 40)
    oversold = tech.rsi < 32 or tech.extension_atr < -2.5
    if held:
        if confirmed_down or (broke_support and total <= -1):
            stance = "EXIT / REDUCE HARD" if horizon != "long_term" else "REDUCE"
            headline = "Trend is broken. Protect capital, then look to rebuy at lower support or after a reclaim."
        elif total <= -2 and oversold:
            stance = "HOLD, SELL INTO THE BOUNCE"
            headline = "Weak setup, but already oversold. Don't sell the low; use a bounce to reduce."
        elif total <= -2:
            stance = "TRIM / SELL AND REBUY LOWER"
            headline = "Setup has deteriorated. Take some off and wait for a level to rebuild."
        elif (extended or rr < 1 or (big_gain and total <= 1)) and not oversold:
            stance = "TRIM INTO STRENGTH"
            headline = "Trend is intact but risk/reward is stretched. Bank part, keep the rest."
        elif total >= 3 and rr >= 1.5:
            stance = "HOLD, ADD ON PULLBACK"
            headline = "Healthy setup. Hold, and add only at the pullback levels below."
        else:
            stance = "HOLD"
            headline = "No strong reason to act. Hold and let the levels below decide."
    else:
        if total >= 3 and rr >= 2 and not extended:
            stance = "BUY / START POSITION"
            headline = "Trend, macro and reward/risk line up. Starter position is reasonable."
        elif total >= 1:
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
    if tech.extension_atr < -2:
        rules.append("Price is stretched below its 20-day average; bounces are sharp but unreliable in a downtrend.")

    alerts = [{"price": stop, "direction": "below", "why": "invalidation / stop"}]
    alerts += [{"price": b["price"], "direction": "below", "why": "pullback level: " + b["why"]} for b in buy_zone[:2]]
    alerts += [{"price": r["price"], "direction": "above", "why": "target / trim: " + r["why"]} for r in res_levels[:2]]
    for al in alerts:
        al["dist_pct"] = round(100 * (al["price"] / price - 1), 1)

    return {
        "stance": stance, "headline": headline, "score": total, "factors": f,
        "levels": {"stop": stop, "support": sup_levels[:5], "resistance": res_levels[:5], "reward_risk": rr},
        "position": {"shares": pos.shares, "cost_basis": pos.cost_basis, "horizon": horizon, "pnl": pnl} if held else None,
        "rules": rules, "alerts": alerts,
        "caveat": "Rule-based heuristics, not a forecast or personalised financial/tax advice.",
    }
