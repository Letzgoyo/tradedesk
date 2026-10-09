"""TradeDesk: personal stock decision-support. Run:  streamlit run app.py"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from tradedesk import alerts, analysis, backtest, briefing, charts, config, data, events, macro, notify, portfolio, profiles
from tradedesk.portfolio import HORIZONS, Position

st.set_page_config(page_title="TradeDesk", page_icon="📈", layout="wide")


# ------------------------------------------------------------------ cached loaders
@st.cache_resource(ttl=900, show_spinner=False)
def load_macro() -> dict:
    return macro.macro_regime()


@st.cache_resource(ttl=900, show_spinner=False)
def load_themes() -> dict:
    return macro.themes_overview()


@st.cache_resource(ttl=900, show_spinner=False)
def load_analysis(query: str, shares: float, cost: float, horizon: str, theme: str):
    return analysis.analyze_ticker(query, load_macro(), Position(query, shares, cost, horizon, "", theme), theme_override=theme)


def safe_analysis(p: Position):
    try:
        return load_analysis(p.ticker, p.shares, p.cost_basis, p.horizon, p.theme), None
    except data.DataError as e:
        return None, str(e)
    except Exception as e:  # keep the page alive if one ticker misbehaves
        return None, f"{p.ticker}: {type(e).__name__}: {e}"


STANCE_ICON = {"HOLD": "🟢", "HOLD, ADD ON PULLBACK": "🟢", "BUY / START POSITION": "🟢",
               "TRIM INTO STRENGTH": "🟡", "HOLD, SELL INTO THE BOUNCE": "🟡", "WAIT FOR PULLBACK": "🟡",
               "TRIM / SELL AND REBUY LOWER": "🟠", "REDUCE": "🟠",
               "EXIT / REDUCE HARD": "🔴", "AVOID / WAIT": "🔴"}
icon = lambda stance: STANCE_ICON.get(stance, "⚪")
REGIME_ICON = {2: "🟢", 1: "🟢", 0: "⚪", -1: "🟠", -2: "🔴"}


# ------------------------------------------------------------------ sidebar
st.sidebar.title("📈 TradeDesk")
PAGES = ["Portfolio", "Stock", "Themes", "Market", "Backtest", "Alerts", "Holdings"]
st.session_state.setdefault("page", "Portfolio")
page = st.sidebar.radio("View", PAGES, key="page")
if st.sidebar.button("Refresh data"):
    st.cache_resource.clear()
    data._cache.clear()
    st.rerun()
st.sidebar.caption("Claude briefing: " + ("on" if briefing.available() else "off (add ANTHROPIC_API_KEY secret)"))
st.sidebar.caption("Phone alerts: " + (", ".join(notify.configured()) or "off (see Alerts)"))
if config.DEMO:
    st.sidebar.warning("DEMO MODE: synthetic prices, not real markets.")
st.sidebar.caption("Daily bars, delayed. Analysis only, not personalised financial or tax advice.")

positions = portfolio.load()


def components_table(comps: list[dict]):
    if comps:
        st.dataframe(pd.DataFrame(comps).rename(columns=str.title), hide_index=True, width="stretch")


# ------------------------------------------------------------------ Holdings editor
def page_holdings():
    st.header("Holdings & watchlist")
    st.write("Add what you own (shares > 0) and what you're watching (shares = 0). Cost basis is per share, in the stock's own "
             "currency. Use the full Yahoo symbol for non-US listings (e.g. `DML.TO`, `BHP.AX`), or type the short ticker and "
             "I'll try to find the listing.")
    df = pd.DataFrame([vars(p) for p in positions]) if positions else pd.DataFrame(
        {"ticker": [], "shares": [], "cost_basis": [], "horizon": [], "theme": [], "notes": []})
    edited = st.data_editor(
        df, num_rows="dynamic", width="stretch", hide_index=True,
        column_config={
            "ticker": st.column_config.TextColumn("Ticker", required=True),
            "shares": st.column_config.NumberColumn("Shares", min_value=0.0, default=0.0),
            "cost_basis": st.column_config.NumberColumn("Cost basis / share", min_value=0.0, default=0.0, format="%.2f"),
            "horizon": st.column_config.SelectboxColumn("Style", options=list(HORIZONS), default="position",
                                                        help="swing = days-weeks, position = weeks-months, long_term = years"),
            "theme": st.column_config.SelectboxColumn("Theme", options=profiles.THEME_CHOICES, default="auto",
                                                      help="What drives this stock. 'auto' detects uranium/gold/silver/copper miners."),
            "notes": st.column_config.TextColumn("Notes"),
        })
    if portfolio.has_seed() and not config.HOLDINGS_FILE.exists():
        st.info("Showing the starter holdings from holdings.seed.json. Press Save to keep them as your own.")
    elif portfolio.has_seed() and st.button("Reload starter holdings (replaces what's above)"):
        portfolio.save(portfolio._read(config.SEED_FILE))
        st.cache_resource.clear()
        st.rerun()
    if st.button("Save", type="primary"):
        new = [Position(str(r.ticker).strip().upper(), float(r.shares or 0), float(r.cost_basis or 0),
                        r.horizon if r.horizon in HORIZONS else "position", str(r.notes or ""),
                        r.theme if r.theme in profiles.THEME_CHOICES else "auto")
               for r in edited.itertuples() if str(r.ticker).strip() and str(r.ticker) != "nan"]
        portfolio.save(new)
        st.cache_resource.clear()
        st.success(f"Saved {len(new)} tickers.")
        st.rerun()
    st.caption("Stored in data/holdings.json on this Repl. Replit deployments can reset local files, so keep a copy somewhere safe.")


# ------------------------------------------------------------------ Portfolio overview
def page_portfolio():
    st.header("Portfolio")
    if not positions:
        st.info("No holdings yet. Add some under **Holdings**.")
        return
    mac = load_macro()
    if mac.get("available"):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Market regime (S&P)", mac["label"], f"score {mac['score']:+d}")
        lv = mac.get("levels", {})
        c2.metric("VIX", lv.get("VIX", "n/a"))
        c3.metric("US 10y yield", f"{lv['US10Y']}%" if "US10Y" in lv else "n/a")
        c4.metric("S&P 500 (SPY)", lv.get("SPY", "n/a"))
    else:
        st.warning(mac.get("note", "Market data unavailable."))

    rows, errors, attention, stale = [], [], [], []
    bar = st.progress(0.0, text="Analysing holdings...")
    for k, p in enumerate(positions):
        bar.progress((k + 1) / len(positions), text=f"Analysing {p.ticker}...")
        an, err = safe_analysis(p)
        if err:
            errors.append(err); continue
        pl, t = an.plan, an.tech
        if an.stale:
            stale.append(an.ticker)
        near = [a for a in pl["alerts"] if abs(a["dist_pct"]) <= max(1.5, 100 * t.atr / t.price)]
        pnl = (pl["position"] or {}).get("pnl")
        sup = t.supports[0].center if t.supports else None
        res = t.resistances[0].center if t.resistances else None
        w = an.htf.get("W")
        rows.append({"": icon(pl["stance"]), "Ticker": an.ticker, "Name": an.name, "Ccy": an.currency, "Price": round(t.price, 2),
                     "1d %": t.chg_1d, "P/L %": pnl["pct"] if pnl else None, "Value": pnl["value"] if pnl else None,
                     "Theme": an.theme.get("label"), f"Theme backdrop": an.theme.get("regime"),
                     "Daily": t.trend["label"], "Weekly": w.trend["label"] if w else "n/a",
                     "Volatility": (t.vol or {}).get("class"), "Stance": pl["stance"],
                     "Stop": pl["levels"]["stop"], "Next support": round(sup, 2) if sup else None,
                     "Next resistance": round(res, 2) if res else None,
                     "Near level": "; ".join(f"{a['why']} ({a['dist_pct']:+.1f}%)" for a in near)})
        if pl["stance"] not in ("HOLD", "HOLD, ADD ON PULLBACK", "AVOID / WAIT") or near:
            attention.append((an.ticker, pl["stance"], pl["headline"], near))
    bar.empty()
    for e in errors:
        st.error(e)
    if stale:
        st.warning("Showing last saved prices (live data unavailable) for: " + ", ".join(stale))
    if attention:
        st.subheader("Needs your attention")
        for tkr, stance, head, near in attention:
            st.markdown(f"**{icon(stance)} {tkr}: {stance}.** {head}"
                        + (f"  \n_Close to: {', '.join(a['why'] for a in near)}_" if near else ""))
    if rows:
        df = pd.DataFrame(rows)
        tot = df.dropna(subset=["Value"]).groupby("Ccy")["Value"].sum()
        if len(tot):
            st.caption("Value of held positions: " + ", ".join(f"{v:,.0f} {c}" for c, v in tot.items()))
        st.dataframe(df, width="stretch", hide_index=True)
        pick = st.selectbox("Open a stock", [r["Ticker"] for r in rows])
        if st.button("Open stock view"):
            st.session_state["stock_pick"] = pick
            st.session_state["page"] = "Stock"
            st.rerun()


# ------------------------------------------------------------------ Stock deep-dive
fmt_money = lambda x: f"{x:,.2f}"


def page_stock():
    st.header("Stock")
    held = {p.ticker: p for p in positions}
    c1, c2 = st.columns([2, 1])
    default = st.session_state.get("stock_pick") or (positions[0].ticker if positions else "AAPL")
    ticker = c1.text_input("Ticker or name (e.g. DNN, DML.TO, FCX, GDX)", value=default).strip().upper()
    st.session_state["stock_pick"] = ticker
    if ticker in held:
        pos = held[ticker]
        c2.caption(f"Using your holding: {pos.shares:g} sh @ {pos.cost_basis:g}, style: {pos.horizon}, theme: {pos.theme}")
    else:
        with c2.expander("Hypothetical position (optional)"):
            sh = st.number_input("Shares", min_value=0.0, value=0.0)
            cb = st.number_input("Cost basis", min_value=0.0, value=0.0)
            hz = st.selectbox("Style", HORIZONS, index=1)
            th = st.selectbox("Theme", profiles.THEME_CHOICES, index=0)
        pos = Position(ticker, sh, cb, hz, "", th)
    if not ticker:
        return
    try:
        with st.spinner(f"Analysing {ticker}..."):
            an = load_analysis(ticker, pos.shares, pos.cost_basis, pos.horizon, pos.theme)
    except data.DataError as e:
        st.error(str(e)); return
    t, pl = an.tech, an.plan
    if an.name:
        st.caption(f"{an.name} ({an.ticker}). Check this is the company you hold: Yahoo reuses short tickers across exchanges.")
    if an.note:
        st.info(an.note + f". Prices are in {an.currency}.")
    if an.stale:
        st.warning(f"Live prices unavailable. Showing {an.source}.")

    st.subheader(f"{icon(pl['stance'])} {pl['stance']}")
    st.write(pl["headline"])
    m = st.columns(6)
    m[0].metric(f"Price ({an.currency})", fmt_money(t.price), f"{t.chg_1d:+.2f}%")
    m[1].metric("Daily trend", t.trend["label"])
    m[2].metric("Weekly trend", an.htf["W"].trend["label"] if an.htf.get("W") else "n/a")
    m[3].metric(f"{an.theme.get('label', 'Theme')} backdrop", an.theme.get("regime", "n/a"))
    m[4].metric("Volatility", (t.vol or {}).get("class", "n/a"), f"~{(t.vol or {}).get('atr_pct', 0)}%/day", delta_color="off")
    pnl = (pl["position"] or {}).get("pnl")
    m[5].metric("Your P/L", f"{pnl['pct']:+.1f}%" if pnl else "n/a", fmt_money(pnl["gain"]) if pnl else None)

    tab_d, tab_w = st.tabs(["Daily chart (weekly zones outlined)", "Weekly chart"])
    with tab_d:
        st.pyplot(charts.render(an), clear_figure=True)
    with tab_w:
        if an.htf.get("W") is not None:
            st.pyplot(charts.render(an, tf="W"), clear_figure=True)
        else:
            st.info("Not enough history for a weekly chart.")

    left, right = st.columns(2)
    with left:
        st.markdown(f"**Why (score {pl['score']:+d} of ±{pl['max_score']})**")
        st.dataframe(pd.DataFrame(pl["factors"]).drop(columns="max").rename(columns=str.title), hide_index=True, width="stretch")
        st.markdown("**If / then plan**")
        for r in pl["rules"]:
            st.markdown(f"- {r}")
    with right:
        st.markdown("**Levels** (★ = weekly/monthly, i.e. major)")
        lv = pl["levels"]
        star = lambda x: ("★ " if x.get("major") else "") + x["why"]
        tbl = ([{"Type": "Stop / invalidation", "Price": lv["stop"], "Why": f"below nearest support, buffer scaled to {(t.vol or {}).get('class', 'normal')} volatility"}]
               + [{"Type": "Resistance / target", "Price": r["price"], "Why": star(r)} for r in lv["resistance"]]
               + [{"Type": "Support / pullback", "Price": s["price"], "Why": star(s)} for s in lv["support"]])
        st.dataframe(pd.DataFrame(tbl), hide_index=True, width="stretch")
        if t.fib:
            f = t.fib
            st.caption(f"Daily Fib leg: {f['direction']}, {f['start']['price']} ({f['start']['date']}) to "
                       f"{f['end']['price']} ({f['end']['date']}). Price sits at {100 * f['current_retrace']:.0f}% retrace.")
        for tf_name, rep in (("Weekly", an.htf.get("W")), ("Monthly", an.htf.get("M"))):
            if rep is not None:
                st.caption(f"{tf_name}: {rep.trend['label']} ({rep.trend['structure']}); RSI {rep.rsi:.0f}"
                           + (f"; {rep.patterns[0]}" if rep.patterns else ""))
        for pat in t.patterns:
            st.markdown(f"- 🔎 {pat}")
        if t.divergence:
            st.markdown(f"- 🔎 RSI divergence, {t.divergence}")
        st.markdown("**Alerts to set in your broker** (or turn on phone alerts under Alerts)")
        st.dataframe(pd.DataFrame(pl["alerts"]).rename(columns={"dist_pct": "Dist %", "price": "Price", "direction": "Dir", "why": "Why"}),
                     hide_index=True, width="stretch")

    with st.expander(f"What drives it: {an.theme.get('label')} backdrop and correlations", expanded=an.theme.get("theme") in profiles.RESOURCE_THEMES):
        st.caption(f"Theme: {an.theme.get('label')} ({an.theme_how}). Change it on the Holdings page if that's wrong.")
        components_table(an.theme.get("components", []))
        if an.theme.get("instruments"):
            st.dataframe(pd.DataFrame(an.theme["instruments"]).rename(columns=str.title), hide_index=True, width="stretch")
        if an.drivers:
            st.markdown("**What it actually moves with (last 90 days, daily returns)**")
            st.dataframe(pd.DataFrame(an.drivers).rename(columns=str.title), hide_index=True, width="stretch")
            top = an.drivers[0]
            st.caption(f"Strongest link right now: {top['symbol']} ({top['role']}), correlation {top['corr']}, beta {top['beta']}. "
                       "Correlations shift; a low number can just mean the stock is trading on its own news.")

    with st.expander("Company: fundamentals, earnings, headlines, upcoming events"):
        mi = an.micro
        if mi.get("next_earnings"):
            st.write(f"**Next earnings:** {mi['next_earnings']}")
        if mi.get("fundamentals"):
            st.json(mi["fundamentals"], expanded=False)
        for label, items in (("Company headlines", (an.news.get("company") or mi.get("news") or [])),
                             (f"{an.theme.get('label')} headlines", an.news.get("theme") or [])):
            if items:
                st.markdown(f"**{label}**")
                for n in items:
                    link = f"[{n['title']}]({n['link']})" if n.get("link") else n["title"]
                    st.markdown(f"- {link}  \n  _{n.get('publisher') or ''} {n.get('date') or ''}_")
        if an.events:
            st.markdown("**Upcoming events (approximate, verify dates)**")
            st.dataframe(pd.DataFrame(an.events), hide_index=True, width="stretch")

    st.divider()
    st.subheader("Claude briefing")
    if not briefing.available():
        st.info("Add your ANTHROPIC_API_KEY as a Replit Secret to get the written backdrop + company + chart briefing.")
        return
    key = f"brief::{an.ticker}::{t.asof}::{pos.shares}::{pos.cost_basis}"
    if st.button("Generate briefing", type="primary"):
        try:
            st.session_state[key] = st.write_stream(briefing.stream_briefing(an.facts()))
        except Exception as e:
            st.error(f"Briefing failed: {type(e).__name__}: {e}")
    elif key in st.session_state:
        st.markdown(st.session_state[key])

    st.subheader("Ask a follow-up")
    hist = st.session_state.setdefault(f"chat::{an.ticker}", [])
    for msg in hist:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    q = st.chat_input(f"Ask about {an.ticker}: e.g. 'where would you rebuy if it drops?'")
    if q:
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            try:
                ans = st.write_stream(briefing.stream_answer(an.facts(), list(hist), q))
                hist += [{"role": "user", "content": q}, {"role": "assistant", "content": ans}]
            except Exception as e:
                st.error(f"Failed: {type(e).__name__}: {e}")

    with st.expander("Cross-check a chart screenshot (e.g. a chart you saw from another analyst)"):
        up = st.file_uploader("PNG / JPG", type=["png", "jpg", "jpeg"])
        if up and st.button("Compare with computed levels"):
            mt = "image/png" if up.name.lower().endswith("png") else "image/jpeg"
            try:
                st.write_stream(briefing.stream_screenshot_check(an.facts(), up.getvalue(), mt))
            except Exception as e:
                st.error(f"Failed: {type(e).__name__}: {e}")


# ------------------------------------------------------------------ Themes (metals & more)
def page_themes():
    st.header("Metals & themes")
    st.caption("The tide each of your stocks actually sits in: the metal, its ETFs, a bellwether, and the dollar/yield headwinds.")
    ov = load_themes()
    summary = [{"": REGIME_ICON.get(v.get("score_norm", 0), "⚪"), "Theme": v["label"], "Backdrop": v.get("regime", "n/a"),
                "Score": v.get("score", 0)} for v in ov.values() if v.get("available")]
    if summary:
        st.dataframe(pd.DataFrame(summary), hide_index=True, width="stretch")
    for key, v in ov.items():
        if not v.get("available"):
            st.warning(f"{v['label']}: no data available")
            continue
        with st.expander(f"{REGIME_ICON.get(v['score_norm'], '⚪')} {v['label']}: {v['regime']}", expanded=False):
            components_table(v["components"])
            st.dataframe(pd.DataFrame(v["instruments"]).rename(columns=str.title), hide_index=True, width="stretch")
    rt = macro.ratios()
    if rt:
        st.subheader("Cross-metal ratios")
        st.dataframe(pd.DataFrame(rt), hide_index=True, width="stretch")
    st.caption("Uranium spot isn't on Yahoo, so the Sprott Physical Uranium Trust (U.UN.TO) stands in as the price proxy. "
               "Futures (GC=F, SI=F, HG=F) are front-month and can jump at contract rolls.")


# ------------------------------------------------------------------ Market
def page_market():
    st.header("Market backdrop (S&P 500)")
    mac = load_macro()
    if not mac.get("available"):
        st.error(mac.get("note", "Market data unavailable.")); return
    st.subheader(f"{mac['label']}  (score {mac['score']:+d}, as of {mac['asof']})")
    components_table(mac["components"])
    c1, c2 = st.columns(2)
    c1.markdown("**Sector leaders (1m):** " + ", ".join(mac["leaders_1m"]))
    c2.markdown("**Sector laggards (1m):** " + ", ".join(mac["laggards_1m"]))
    sect = pd.DataFrame(mac["sectors"]).T.rename(columns={"name": "Sector", "1m": "1m %", "3m": "3m %", "above_50d": "Above 50d"})
    st.dataframe(sect.sort_values("1m %", ascending=False), width="stretch")
    st.subheader("Next 3 weeks")
    ev = events.upcoming(21)
    if ev:
        st.dataframe(pd.DataFrame(ev), hide_index=True, width="stretch")
    st.caption("FOMC and jobs-report dates are built in and approximate: verify on federalreserve.gov and bls.gov. "
               "Add your own (CPI, earnings, central-bank meetings) in data/events.json.")


# ------------------------------------------------------------------ Backtest
def page_backtest():
    st.header("Backtest")
    st.write("Replays the hold/trim/exit calls through history using only what was known at each date, then compares "
             "'follow the stance' with simply holding. Run it on each of your stocks before you trust a stance.")
    c1, c2, c3 = st.columns(3)
    q = c1.text_input("Ticker", value=st.session_state.get("stock_pick") or (positions[0].ticker if positions else "AAPL")).strip().upper()
    years = c2.slider("Years of history", 2, 8, 5)
    theme = c3.selectbox("Theme", profiles.THEME_CHOICES)
    key = f"bt::{q}::{years}::{theme}"
    if st.button("Run backtest", type="primary"):
        bar = st.progress(0.0, text="Replaying history...")
        try:
            st.session_state[key] = backtest.run(q, theme, years, progress=lambda f: bar.progress(f, text="Replaying history..."))
        except data.DataError as e:
            st.error(str(e))
        bar.empty()
    res = st.session_state.get(key)
    if not res:
        return
    s = res["summary"]
    st.subheader(f"{res['symbol']} ({profiles.THEMES[res['theme']]['label']}), {s['years']} years, {s['decisions']} decision points")
    a, b = s["strategy"], s["buy_hold"]
    m = st.columns(4)
    m[0].metric("Follow the stance: return", f"{a['total_return_pct']}%", f"{a['total_return_pct'] - b['total_return_pct']:+.1f} vs hold")
    m[1].metric("Buy and hold: return", f"{b['total_return_pct']}%")
    m[2].metric("Worst drawdown (stance / hold)", f"{a['max_drawdown_pct']}% / {b['max_drawdown_pct']}%")
    m[3].metric("Average exposure", f"{a['avg_exposure_pct']}%")
    st.line_chart(res["equity"][["Follow the stance", "Buy and hold"]])
    for v in res["verdict"]:
        st.markdown(f"- {v}")
    st.markdown("**What happened after each stance**")
    bk = res["buckets"].copy()
    bk = bk.rename(columns={"stance": "Stance", "signals": "Signals", "hit_rate": "Up after ~3m", "worst_dip": "Avg worst dip (1m)"})
    for c in bk.columns:
        if c.startswith("fwd"):
            bk[c] = (100 * bk[c]).round(1)
    bk = bk.rename(columns={c: f"Avg next {c[3:]} bars %" for c in bk.columns if c.startswith("fwd")})
    bk["Up after ~3m"] = (100 * bk["Up after ~3m"]).round(0)
    bk["Avg worst dip (1m)"] = (100 * bk["Avg worst dip (1m)"]).round(1)
    st.dataframe(bk, hide_index=True, width="stretch")
    base = res["baseline"]
    st.caption("Baseline (any day): " + ", ".join(f"{k[3:]}-bar {100 * v:+.1f}%" for k, v in base.items() if k.startswith("fwd")))
    st.markdown("**Stable across time?** (first 60% of the period vs last 40%)")
    seg = [{"Period": k.replace("_", " "), "Stance return %": v["strategy"]["total_return_pct"], "Hold return %": v["buy_hold"]["total_return_pct"],
            "Stance drawdown %": v["strategy"]["max_drawdown_pct"], "Hold drawdown %": v["buy_hold"]["max_drawdown_pct"]}
           for k, v in res["segments"].items()]
    st.dataframe(pd.DataFrame(seg), hide_index=True, width="stretch")


# ------------------------------------------------------------------ Alerts
def page_alerts():
    st.header("Phone alerts")
    ch = notify.configured()
    if ch:
        st.success("Configured: " + ", ".join(ch))
    else:
        st.warning("No channel configured yet.")
    st.markdown("""
**Easiest setup (free, 2 minutes): ntfy**
1. Install the **ntfy** app on your phone and subscribe to a long, random topic name (e.g. `tradedesk-k3j9x2m7q`). Anyone who knows the name can read it, so make it unguessable.
2. In Replit **Secrets** add `NTFY_TOPIC` with that topic name. Restart the Repl.
3. Press **Send test** below.

Telegram (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`) and Discord (`DISCORD_WEBHOOK_URL`) also work.

**What triggers a message:** a stop, pullback/rebuy or target level being hit; price coming within 1% of one; or a stance change
(e.g. HOLD becoming TRIM). Each level fires once, then re-arms after price moves a full ATR back. The loop checks every 30 minutes
while the Repl is running. On the free plan Repls sleep, so for dependable alerts use a Replit Deployment, or run
`python run_alerts.py` as a **Scheduled Deployment**.
""")
    c1, c2 = st.columns(2)
    if c1.button("Send test"):
        res = notify.send("TradeDesk test", "If you can read this, alerts are working.")
        st.write(res or "No channel configured.")
    if c2.button("Check my holdings now"):
        import run_alerts
        with st.spinner("Checking..."):
            n = run_alerts.one_pass()
        st.success(f"Done: {n} alert(s) raised." + ("" if ch else " (nothing sent: no channel configured)"))
    log = alerts.load_log()
    if log:
        st.subheader("Recent alerts")
        st.dataframe(pd.DataFrame(log[::-1])[["time", "ticker", "kind", "title", "body"]], hide_index=True, width="stretch")


{"Portfolio": page_portfolio, "Stock": page_stock, "Themes": page_themes, "Market": page_market,
 "Backtest": page_backtest, "Alerts": page_alerts, "Holdings": page_holdings}[page]()
