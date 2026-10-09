import json

import pandas as pd
import pytest

from tradedesk import config, data, events, news, profiles, providers, symbols


# ---------------------------------------------------------------- symbols
def test_resolver_falls_through_to_the_listing_that_has_data(tmp_data):
    loaded = []

    def probe(sym):
        loaded.append(sym)
        if sym != "DML.TO":
            raise RuntimeError("no data")
        return "frame"

    sym, df, note = symbols.resolve("dml", probe, search=lambda q: [])
    assert sym == "DML.TO" and df == "frame"
    assert "resolved to DML.TO" in note and "DNN" in note  # tells you the other listing exists
    assert loaded[0] == "DML"  # tried what you typed first
    assert symbols.remembered("DML") == "DML.TO"  # and remembers it


def test_resolver_error_is_actionable(tmp_data):
    with pytest.raises(LookupError) as e:
        symbols.resolve("ZZZZ", lambda s: (_ for _ in ()).throw(RuntimeError("x")), search=lambda q: [])
    assert "DML.TO" in str(e.value) and "Tried" in str(e.value)


def test_candidates_dedupe_and_suffixes():
    c = symbols.candidates("DML", search=lambda q: [("DML.TO", "Denison")])
    assert c[0] == "DML" and c.count("DML.TO") == 1 and ".AX" in "".join(c)
    assert symbols.candidates("^VIX", search=lambda q: []) == ["^VIX"]
    assert symbols.currency_for("DML.TO") == "CAD" and symbols.currency_for("BHP.AX") == "AUD" and symbols.currency_for("CCJ") == "USD"


# ---------------------------------------------------------------- themes
def test_theme_detection_precedence():
    assert profiles.detect_theme("DML.TO")[0] == "uranium"           # known ticker, suffix ignored
    assert profiles.detect_theme("DNN")[0] == "uranium"
    assert profiles.detect_theme("DNN", override="gold") == ("gold", "set by you")
    micro = {"fundamentals": {"industry": "Other Precious Metals & Mining", "shortName": "Foo Gold Corp"}}
    assert profiles.detect_theme("FOO", micro)[0] == "gold"           # falls back to name
    assert profiles.detect_theme("FOO", {"fundamentals": {"sector": "Technology"}})[0] == "tech"
    assert profiles.detect_theme("UNKNOWN")[0] == "broad"
    for t in profiles.THEMES:
        assert profiles.instruments(t) or t in ("broad",) or profiles.THEMES[t]["etfs"]


# ---------------------------------------------------------------- providers (parsers; network is not used)
def test_parse_stooq_and_polygon():
    csv = "Date,Open,High,Low,Close,Volume\n2026-01-02,10,11,9,10.5,1000\n2026-01-05,10.5,12,10,11,2000\n"
    df = providers.parse_stooq_csv(csv)
    assert list(df["Close"]) == [10.5, 11.0]
    with pytest.raises(RuntimeError):
        providers.parse_stooq_csv("No data")
    pg = providers.parse_polygon({"status": "OK", "results": [{"t": 1767312000000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}]})
    assert pg.iloc[0]["Close"] == 1.5
    assert providers.stooq_symbol("AAPL") == "aapl.us" and providers.stooq_symbol("BP.L") == "bp.uk"
    assert providers.stooq_symbol("GC=F") is None and providers.stooq_symbol("DML.TO") is None


def test_stale_disk_cache_is_served_when_every_provider_fails(tmp_data, monkeypatch):
    monkeypatch.setattr(config, "DEMO", False)
    good = data.synthetic_prices("ABC", n=300)
    data._write_disk("ABC", "10y", good)
    data._cache.clear()
    monkeypatch.setattr(providers, "chain", lambda: [("down", lambda t, p: (_ for _ in ()).throw(RuntimeError("boom")))])
    df = data.get_prices("ABC")
    assert df.attrs["stale"] and "stale cache" in df.attrs["source"]
    with pytest.raises(data.DataError):
        data.get_prices("NEVER_CACHED")


def test_successful_fetch_is_cached_to_disk(tmp_data, monkeypatch):
    monkeypatch.setattr(config, "DEMO", False)
    data._cache.clear()
    raw = data.synthetic_prices("OKK", n=300)
    monkeypatch.setattr(providers, "chain", lambda: [("fake", lambda t, p: raw)])
    df = data.get_prices("OKK")
    assert df.attrs["source"] == "fake" and not df.attrs.get("stale")
    assert data._read_disk("OKK", "10y") is not None


def test_resample_weekly_monthly():
    df = data.synthetic_prices("RS", n=600)
    w, m = data.resample(df, "W"), data.resample(df, "M")
    assert 100 < len(w) < 140 and 20 < len(m) < 35
    assert w["High"].max() == pytest.approx(df["High"].max()) and m["Volume"].sum() == pytest.approx(df["Volume"].sum())


# ---------------------------------------------------------------- news / events
def test_google_news_rss_parser():
    xml = """<rss><channel>
    <item><title>Denison hits milestone &amp; more - Mining.com</title><link>http://x/1</link><pubDate>Mon, 05 Oct 2026</pubDate><source>Mining.com</source></item>
    <item><title></title></item></channel></rss>"""
    out = news.parse_rss(xml)
    assert len(out) == 1 and out[0]["title"] == "Denison hits milestone & more" and out[0]["publisher"] == "Mining.com"
    assert news.parse_rss("not xml") == []


def test_events_window_and_custom_file(tmp_data):
    from datetime import date
    (tmp_data / "events.json").write_text(json.dumps([{"date": "2026-11-12", "event": "US CPI", "note": ""}]))
    ev = events.upcoming(days=40, today=date(2026, 10, 9))  # window runs to 2026-11-18
    names = [e["event"] for e in ev]
    assert "FOMC rate decision" in names and "US CPI" in names  # Oct 28 FOMC and the custom 12 Nov CPI line
    assert [e["date"] for e in ev] == sorted(e["date"] for e in ev)
    assert all("2026-10-09" <= e["date"] <= "2026-11-18" for e in ev)
    assert "US CPI" not in [e["event"] for e in events.upcoming(days=30, today=date(2026, 10, 9))]  # outside a 30-day window
