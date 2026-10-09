import numpy as np
import pandas as pd
import pytest

from tradedesk import analysis, data, macro, plan, technicals as ta
from tradedesk.portfolio import Position


def ohlc(closes, spread=0.01):
    c = np.asarray(closes, float)
    idx = pd.bdate_range(end="2026-10-09", periods=len(c))
    return pd.DataFrame({"Open": c, "High": c * (1 + spread), "Low": c * (1 - spread), "Close": c,
                         "Volume": np.full(len(c), 1e6)}, index=idx)


def zigzag(points, per_leg=25):
    out = []
    for a, b in zip(points[:-1], points[1:]):
        out += list(np.linspace(a, b, per_leg, endpoint=False))
    return out + [points[-1]]


def test_swings_alternate_and_hit_the_turning_points():
    df = ohlc(zigzag([100, 150, 120, 170, 130, 160]))
    sw = ta.find_swings(df)
    kinds = [s.kind for s in sw]
    assert all(a != b for a, b in zip(kinds, kinds[1:]))
    confirmed = [round(s.price / 10) * 10 for s in sw if s.confirmed]
    assert confirmed[:4] == [100, 150, 120, 170]


def test_fib_retracement_math_on_known_leg():
    df = ohlc(zigzag([100, 200, 150]))
    r = ta.analyze(df)
    assert r.fib and r.fib["up"]
    ret = r.fib["retracements"]
    hi = r.fib["end"]["price"]; lo = r.fib["start"]["price"]
    assert ret["0.5"] == pytest.approx(hi - 0.5 * (hi - lo), abs=0.01)
    assert ret["0.618"] == pytest.approx(hi - 0.618 * (hi - lo), abs=0.01)
    assert r.fib["extensions"]["1.618"] == pytest.approx(lo + 1.618 * (hi - lo), abs=0.01)


def test_zone_clusters_repeated_turning_points():
    df = ohlc(zigzag([100, 150, 100, 152, 101, 151, 100, 140]))
    r = ta.analyze(df)
    res = [z for z in r.zones if z.kind in ("resistance", "at") and z.center > 140]
    assert res and max(z.touches for z in res) >= 3


def test_uptrend_and_downtrend_labels():
    up = ta.analyze(ohlc(zigzag([50, 80, 70, 110, 95, 140, 125, 170, 155, 200], 30)))
    down = ta.analyze(ohlc(zigzag([200, 170, 180, 140, 150, 110, 120, 80, 90, 50], 30)))
    assert up.trend["score"] >= 2 and "up" in up.trend["label"].lower()
    assert down.trend["score"] <= -2 and "down" in down.trend["label"].lower()


def test_rsi_bounds():
    df = data.synthetic_prices("TEST")
    r = ta.rsi(df["Close"]).dropna()
    assert r.between(0, 100).all()


def test_plan_exits_confirmed_downtrend_and_waits_when_flat_watchlist():
    mac = {"label": "Risk-off", "score_norm": -2}
    down = ta.analyze(ohlc(zigzag([200, 170, 180, 140, 150, 110, 120, 80, 90, 50], 30)))
    p = plan.build_plan(down, mac, {"score": -2, "why": "laggard"}, Position("X", 10, 150))
    assert p["stance"].startswith(("EXIT", "REDUCE", "HOLD, SELL", "TRIM"))
    w = plan.build_plan(down, mac, {"score": -2, "why": "laggard"}, Position("X", 0, 0))
    assert w["stance"] in ("AVOID / WAIT", "WAIT FOR PULLBACK")


def test_plan_levels_are_ordered_and_sane():
    df = data.synthetic_prices("AAPL")
    an = analysis.analyze_ticker("AAPL", macro.macro_regime(), Position("AAPL", 10, 100))
    lv = an.plan["levels"]
    px = an.tech.price
    assert lv["stop"] < px
    assert all(r["price"] > px for r in lv["resistance"])
    assert all(s["price"] < px for s in lv["support"])
    assert an.plan["rules"] and an.plan["alerts"]


def test_macro_regime_in_demo_has_components():
    m = macro.macro_regime()
    assert m["available"] and m["components"] and m["label"]


def test_facts_are_json_serialisable():
    import json
    an = analysis.analyze_ticker("MSFT", macro.macro_regime(), Position("MSFT", 5, 300))
    json.dumps(an.facts(), default=str)
