import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from tradedesk import alerts, analysis, backtest, data, macro, notify, plan, technicals as ta
from tradedesk.portfolio import Position
from test_engine import ohlc, zigzag


# ---------------------------------------------------------------- weekly / monthly + volatility
def test_weekly_and_monthly_reports_exist_and_use_their_own_settings():
    full = data.synthetic_prices("MTF")
    htf, dfw = analysis.htf_reports(full)
    assert htf["W"].tf == "W" and htf["M"].tf == "M" and dfw is not None
    assert htf["W"].trend["sma200"] is None or htf["W"].trend["sma200"] > 0
    assert htf["W"].vol is None  # volatility class is a daily concept


def test_weekly_zone_is_found_where_weekly_swings_cluster():
    # three weekly-scale tops near 150 over ~3 years of daily bars
    pts = [100, 150, 105, 151, 100, 149, 110, 152, 120, 140]
    df = ohlc(zigzag(pts, per_leg=80))
    rep = ta.analyze(data.resample(df, "W"), "W")
    near = [z for z in rep.zones if 140 <= z.center <= 160]
    assert near and max(z.touches for z in near) >= 2


def test_vol_class_and_stop_widens_for_jumpy_stocks():
    calm = ohlc(np.linspace(100, 130, 300), spread=0.004)
    wild = ohlc(100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0.0008, 0.05, 300))), spread=0.05)
    assert ta.vol_profile(calm)["class"] in ("low", "normal")
    assert ta.vol_profile(wild)["class"] in ("high", "extreme")
    mac = {"label": "Mixed", "score_norm": 0}
    stops = {}
    for name, df in (("calm", calm), ("wild", wild)):
        r = ta.analyze(df)
        p = plan.build_plan(r, mac, {"score": 0, "why": ""}, Position("X", 1, 1))
        stops[name] = (r.price - p["levels"]["stop"]) / r.atr
    assert stops["wild"] >= 2.0 and stops["calm"] >= 1.5   # never closer than the minimum ATR distance
    assert stops["wild"] >= stops["calm"] - 0.01


# ---------------------------------------------------------------- themes in the plan
def test_resource_stock_follows_its_metal_not_just_the_sp500():
    full = data.synthetic_prices("DNN")
    r = ta.analyze(full.iloc[-756:])
    rs = {"score": 0, "why": ""}
    bullish_metal = {"theme": "uranium", "label": "Uranium", "available": True, "score_norm": 2, "regime": "Strong tailwind", "components": []}
    bearish_metal = {**bullish_metal, "score_norm": -2, "regime": "Strong headwind"}
    risk_on = {"label": "Risk-on", "score_norm": 2}
    up = plan.build_plan(r, risk_on, rs, Position("DNN", 1, 1), theme=bullish_metal)
    dn = plan.build_plan(r, risk_on, rs, Position("DNN", 1, 1), theme=bearish_metal)
    assert dn["score"] < up["score"]                      # metal headwind lowers the score even when the S&P is risk-on
    names = [f["factor"] for f in up["factors"]]
    assert any("Uranium backdrop" in n for n in names) and "Market backdrop" in names
    mk = next(f for f in up["factors"] if f["factor"] == "Market backdrop")
    assert mk["max"] == 1                                 # S&P gets half weight for a resource stock


def test_theme_regime_demo_returns_components_and_instruments():
    for t in ("gold", "silver", "copper", "uranium"):
        reg = macro.theme_regime(t)
        assert reg["available"] and reg["components"] and reg["regime"]
    assert macro.ratios()


def test_driver_correlations_sorted_and_bounded():
    df = data.synthetic_prices("GOLDCO")
    d = analysis.driver_correlations(df, "gold")
    assert d and all(-1 <= x["corr"] <= 1 for x in d)
    assert [abs(x["corr"]) for x in d] == sorted((abs(x["corr"]) for x in d), reverse=True)


# ---------------------------------------------------------------- backtest
def test_backtest_stance_never_sees_the_future():
    full = data.synthetic_prices("LOOK")
    i = len(full) - 200
    a = backtest.stance_at(full, i, "LOOK", "gold")
    mutated = full.copy()
    mutated.iloc[i + 1:, :4] = mutated.iloc[i + 1:, :4] * 3.0   # wreck every price after the decision date
    b = backtest.stance_at(mutated, i, "LOOK", "gold")
    assert a == b


def test_backtest_runs_and_is_internally_consistent():
    res = backtest.run("BTEST", theme="gold", years=3, step=10)
    s = res["summary"]
    assert s["decisions"] > 40 and res["verdict"]
    eq = res["equity"]
    assert (eq["Exposure"].between(0, 1)).all()
    assert eq["Buy and hold"].iloc[-1] > 0 and eq["Follow the stance"].iloc[-1] > 0
    # exposure-weighted return can't exceed the best single day by construction: sanity check on stats
    assert s["strategy"]["max_drawdown_pct"] <= 0 and s["buy_hold"]["max_drawdown_pct"] <= 0
    assert set(res["segments"]) == {"first_60pct", "last_40pct"}
    assert res["buckets"]["signals"].sum() == s["decisions"]


# ---------------------------------------------------------------- alerts
class FakeAn:
    def __init__(self, price, stance="HOLD", level=100.0, direction="below", atr=2.0):
        self.ticker = "TST"
        self.tech = type("T", (), {"price": price, "atr": atr})()
        self.plan = {"stance": stance, "headline": "h",
                     "alerts": [{"price": level, "direction": direction, "why": "invalidation / stop", "dist_pct": 0}]}


def test_cross_fires_once_then_rearms_after_an_atr_away(tmp_data):
    state = {"levels": {}, "stance": {}}
    t0 = datetime(2026, 10, 9, tzinfo=timezone.utc)
    assert alerts.evaluate(FakeAn(110), state, t0) == []                                   # far away: quiet
    ev = alerts.evaluate(FakeAn(99), state, t0 + timedelta(hours=1))
    assert [e["kind"] for e in ev] == ["cross"] and ev[0]["urgent"]
    assert alerts.evaluate(FakeAn(98), state, t0 + timedelta(hours=2)) == []               # still below: no spam
    assert alerts.evaluate(FakeAn(99), state, t0 + timedelta(hours=48)) == []              # not re-armed yet
    alerts.evaluate(FakeAn(103), state, t0 + timedelta(hours=50))                          # >1 ATR above: re-arms
    assert [e["kind"] for e in alerts.evaluate(FakeAn(99), state, t0 + timedelta(hours=75))] == ["cross"]


def test_approach_and_stance_change(tmp_data):
    state = {"levels": {}, "stance": {}}
    t0 = datetime(2026, 10, 9, tzinfo=timezone.utc)
    ev = alerts.evaluate(FakeAn(100.8), state, t0)
    assert [e["kind"] for e in ev] == ["approach"]
    ev = alerts.evaluate(FakeAn(130, stance="EXIT / REDUCE HARD"), state, t0 + timedelta(hours=30))
    assert [e["kind"] for e in ev] == ["stance"] and ev[0]["urgent"]


def test_above_direction_and_state_roundtrip(tmp_data):
    state = alerts.load_state()
    ev = alerts.evaluate(FakeAn(105, level=104, direction="above"), state)
    assert ev and ev[0]["kind"] == "cross"
    alerts.save_state(state)
    assert alerts.load_state()["levels"]
    alerts.append_log(ev)
    assert alerts.load_log()[-1]["ticker"] == "TST"


def test_notify_channels_and_error_isolation(monkeypatch):
    for k in ("NTFY_TOPIC", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)
    assert notify.configured() == [] and notify.send("t", "b") == []
    sent = []
    monkeypatch.setenv("NTFY_TOPIC", "topic-xyz")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.invalid/hook")
    monkeypatch.setattr(notify, "_post", lambda url, data, headers=None, timeout=15: sent.append((url, data, headers)))
    res = notify.send("Stance ✓ changed", "body ✓", urgent=True)
    assert [r["channel"] for r in res] == ["ntfy", "discord"] and all(r["ok"] for r in res)
    ntfy = sent[0]
    assert ntfy[0].endswith("/topic-xyz") and ntfy[2]["Priority"] == "high" and ntfy[2]["Title"].isascii()

    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(notify, "_post", boom)
    res = notify.send("t", "b")
    assert all(not r["ok"] for r in res)  # reported, not raised


def test_seed_holdings_load_until_user_saves(tmp_data, monkeypatch):
    from tradedesk import config, portfolio
    seed = tmp_data / "seed.json"
    seed.write_text(json.dumps([{"ticker": "AAA", "shares": 5, "cost_basis": 2.5, "theme": "gold"}]))
    monkeypatch.setattr(config, "SEED_FILE", seed)
    assert [p.ticker for p in portfolio.load()] == ["AAA"]            # no saved file: starter list
    portfolio.save([portfolio.Position("BBB", 1, 1)])
    assert [p.ticker for p in portfolio.load()] == ["BBB"]            # your own save wins
    monkeypatch.setattr(config, "SEED_FILE", tmp_data / "missing.json")
    (tmp_data / "holdings.json").unlink()
    assert portfolio.load() == []


def test_real_seed_file_is_consistent_with_the_screenshot():
    from tradedesk import config, portfolio
    pos = {p.ticker: p for p in portfolio._read(config.SEED_FILE)}
    assert len(pos) == 9
    assert round(pos["DML.TO"].shares * pos["DML.TO"].cost_basis) == 1970      # matches broker cost basis
    assert round(pos["VCU.V"].shares * pos["VCU.V"].cost_basis) == 1696
    assert round(pos["ELE.TO"].shares * pos["ELE.TO"].cost_basis) == 1714
