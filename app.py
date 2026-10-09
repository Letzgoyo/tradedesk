"""TradeDesk: personal stock decision-support. Run:  streamlit run app.py"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from tradedesk import analysis, briefing, charts, config, data, macro, portfolio
from tradedesk.portfolio import HORIZONS, Position

st.set_page_config(page_title="TradeDesk", page_icon="📈", layout="wide")


# ------------------------------------------------------------------ cached loaders
@st.cache_resource(ttl=900, show_spinner=False)
def load_macro() -> dict:
    return macro.macro_regime()


@st.cache_resource(ttl=900, show_spinner=False)
def load_analysis(ticker: str, shares: float, cost: float, horizon: str):
    pos = Position(ticker, shares, cost, horizon)
    return analysis.analyze_ticker(ticker, load_macro(), pos)


def safe_analysis(p: Position):
    try:
        return load_analysis(p.ticker, p.shares, p.cost_basis, p.horizon), None
    except data.DataError as e:
        return None, str(e)
    except Exception as e:  # keep the page alive if one ticker misbehaves
        return None, f"{p.ticker}: {type(e).__name__}: {e}"


STANCE_ICON = {"HOLD": "🟢", "HOLD, ADD ON PULLBACK": "🟢", "BUY / START POSITION": "🟢",
               "TRIM INTO STRENGTH": "🟡", "HOLD, SELL INTO THE BOUNCE": "🟡", "WAIT FOR PULLBACK": "🟡",
               "TRIM / SELL AND REBUY LOWER": "🟠", "REDUCE": "🟠",
               "EXIT / REDUCE HARD": "🔴", "AVOID / WAIT": "🔴"}


def icon(stance: str) -> str:
    return STANCE_ICON.get(stance, "⚪")


# ------------------------------------------------------------------ sidebar
st.sidebar.title("📈 TradeDesk")
PAGES = ["Portfolio", "Stock", "Market", "Holdings"]
st.session_state.setdefault("page", "Portfolio")
page = st.sidebar.radio("View", PAGES, key="page")
if st.sidebar.button("Refresh data"):
    st.cache_resource.clear()
    data._cache.clear()
    st.rerun()
st.sidebar.caption("Claude briefing: " + ("on" if briefing.available() else "off (add ANTHROPIC_API_KEY secret)"))
if config.DEMO:
    st.sidebar.warning("DEMO MODE: synthetic prices, not real markets.")
st.sidebar.caption("Daily bars, delayed. Analysis only, not personalised financial or tax advice.")

positions = portfolio.load()


# ------------------------------------------------------------------ Holdings editor
def page_holdings():
    st.header("Holdings & watchlist")
    st.write("Add what you own (shares > 0) and what you're watching (shares = 0). Cost basis is per share.")
    df = pd.DataFrame([vars(p) for p in positions]) if positions else pd.DataFrame(
        {"ticker": [], "shares": [], "cost_basis": [], "horizon": [], "notes": []})
    edited = st.data_editor(
        df, num_rows="dynamic", use_container_width=True, hide_index=True,
        column_config={
            "ticker": st.column_config.TextColumn("Ticker", required=True),
            "shares": st.column_config.NumberColumn("Shares", min_value=0.0, default=0.0),
            "cost_basis": st.column_config.NumberColumn("Cost basis / share", min_value=0.0, default=0.0, format="%.2f"),
            "horizon": st.column_config.SelectboxColumn("Style", options=list(HORIZONS), default="position",
                                                        help="swing = days-weeks, position = weeks-months, long_term = years"),
            "notes": st.column_config.TextColumn("Notes"),
        })
    if st.button("Save", type="primary"):
        new = [Position(str(r.ticker).strip().upper(), float(r.shares or 0), float(r.cost_basis or 0),
                        r.horizon if r.horizon in HORIZONS else "position", str(r.notes or ""))
               for r in edited.itertuples() if str(r.ticker).strip() and str(r.ticker) != "nan"]
        portfolio.save(new)
        st.cache_resource.clear()
        st.success(f"Saved {len(new)} tickers.")
        st.rerun()
    st.caption("Stored in data/holdings.json on this Repl. On Replit, deployments can reset local files, so "
               "keep a copy of your holdings somewhere safe.")


# ------------------------------------------------------------------ Portfolio overview
def page_portfolio():
    st.header("Portfolio")
    if not positions:
        st.info("No holdings yet. Add some under **Holdings**.")
        return
    mac = load_macro()
    if mac.get("available"):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Market regime", mac["label"], f"score {mac['score']:+d}")
        lv = mac.get("levels", {})
        c2.metric("VIX", lv.get("VIX", "n/a"))
        c3.metric("US 10y yield", f"{lv['US10Y']}%" if "US10Y" in lv else "n/a")
        c4.metric("S&P 500 (SPY)", lv.get("SPY", "n/a"))
    else:
        st.warning(mac.get("note", "Market data unavailable."))

    rows, errors, attention = [], [], []
    bar = st.progress(0.0, text="Analysing holdings...")
    for k, p in enumerate(positions):
        bar.progress((k + 1) / len(positions), text=f"Analysing {p.ticker}...")
        an, err = safe_analysis(p)
        if err:
            errors.append(err); continue
        pl, t = an.plan, an.tech
        near = [a for a in pl["alerts"] if abs(a["dist_pct"]) <= max(1.5, 100 * t.atr / t.price)]
        pnl = (pl["position"] or {}).get("pnl")
        sup = t.supports[0].center if t.supports else None
        res = t.resistances[0].center if t.resistances else None
        rows.append({"": icon(pl["stance"]), "Ticker": p.ticker, "Price": round(t.price, 2), "1d %": t.chg_1d,
                     "P/L %": pnl["pct"] if pnl else None, "Value": pnl["value"] if pnl else None,
                     "Trend": t.trend["label"], "Stance": pl["stance"],
                     "Stop": pl["levels"]["stop"], "Next support": round(sup, 2) if sup else None,
                     "Next resistance": round(res, 2) if res else None,
                     "Near level": "; ".join(f"{a['why']} ({a['dist_pct']:+.1f}%)" for a in near)})
        if pl["stance"] not in ("HOLD", "HOLD, ADD ON PULLBACK", "AVOID / WAIT") or near:
            attention.append((p.ticker, pl["stance"], pl["headline"], near))
    bar.empty()
    for e in errors:
        st.error(e)
    if attention:
        st.subheader("Needs your attention")
        for tkr, stance, head, near in attention:
            st.markdown(f"**{icon(stance)} {tkr}: {stance}.** {head}"
                        + (f"  \n_Close to: {', '.join(a['why'] for a in near)}_" if near else ""))
    if rows:
        df = pd.DataFrame(rows)
        total = df["Value"].dropna().sum()
        if total:
            st.caption(f"Total value of held positions: ${total:,.0f}")
        st.dataframe(df, use_container_width=True, hide_index=True)
        pick = st.selectbox("Open a stock", [r["Ticker"] for r in rows])
        if st.button("Open stock view"):
            st.session_state["stock_pick"] = pick
            st.session_state["page"] = "Stock"
            st.rerun()


# ------------------------------------------------------------------ Stock deep-dive
def fmt_money(x):
    return f"${x:,.2f}"


def page_stock():
    st.header("Stock")
    held = {p.ticker: p for p in positions}
    c1, c2 = st.columns([2, 1])
    default = st.session_state.get("stock_pick") or (positions[0].ticker if positions else "AAPL")
    ticker = c1.text_input("Ticker", value=default).strip().upper()
    st.session_state["stock_pick"] = ticker
    if ticker in held:
        pos = held[ticker]
        c2.caption(f"Using your holding: {pos.shares:g} sh @ {pos.cost_basis:g}, style: {pos.horizon}")
    else:
        with c2.expander("Hypothetical position (optional)"):
            sh = st.number_input("Shares", min_value=0.0, value=0.0)
            cb = st.number_input("Cost basis", min_value=0.0, value=0.0)
            hz = st.selectbox("Style", HORIZONS, index=1)
        pos = Position(ticker, sh, cb, hz)
    if not ticker:
        return
    try:
        with st.spinner(f"Analysing {ticker}..."):
            an = load_analysis(ticker, pos.shares, pos.cost_basis, pos.horizon)
    except data.DataError as e:
        st.error(str(e)); return
    t, pl = an.tech, an.plan

    st.subheader(f"{icon(pl['stance'])} {pl['stance']}")
    st.write(pl["headline"])
    m = st.columns(5)
    m[0].metric("Price", fmt_money(t.price), f"{t.chg_1d:+.2f}%")
    m[1].metric("Trend", t.trend["label"])
    m[2].metric("RSI(14)", f"{t.rsi:.0f}")
    m[3].metric("Market regime", an.macro.get("label", "n/a"))
    pnl = (pl["position"] or {}).get("pnl")
    m[4].metric("Your P/L", f"{pnl['pct']:+.1f}%" if pnl else "n/a", fmt_money(pnl["gain"]) if pnl else None)

    st.pyplot(charts.render(an), clear_figure=True)

    left, right = st.columns(2)
    with left:
        st.markdown("**Why (score " + f"{pl['score']:+d})**")
        st.dataframe(pd.DataFrame(pl["factors"]).rename(columns=str.title), hide_index=True, use_container_width=True)
        st.markdown("**If / then plan**")
        for r in pl["rules"]:
            st.markdown(f"- {r}")
    with right:
        st.markdown("**Levels**")
        lv = pl["levels"]
        tbl = ([{"Type": "Stop / invalidation", "Price": lv["stop"], "Why": "below nearest support less 0.5 ATR"}]
               + [{"Type": "Resistance / target", "Price": r["price"], "Why": r["why"]} for r in lv["resistance"]]
               + [{"Type": "Support / pullback", "Price": s["price"], "Why": s["why"]} for s in lv["support"]])
        st.dataframe(pd.DataFrame(tbl), hide_index=True, use_container_width=True)
        if t.fib:
            f = t.fib
            st.caption(f"Fib leg: {f['direction']}, {f['start']['price']} ({f['start']['date']}) to "
                       f"{f['end']['price']} ({f['end']['date']}). Price sits at {100 * f['current_retrace']:.0f}% retrace.")
        for pat in t.patterns:
            st.markdown(f"- 🔎 {pat}")
        if t.divergence:
            st.markdown(f"- 🔎 RSI divergence, {t.divergence}")
        st.markdown("**Alerts to set in your broker**")
        st.dataframe(pd.DataFrame(pl["alerts"]).rename(columns={"dist_pct": "Dist %", "price": "Price",
                                                                 "direction": "Dir", "why": "Why"}),
                     hide_index=True, use_container_width=True)

    with st.expander("Company: fundamentals, earnings, headlines"):
        mi = an.micro
        if mi.get("next_earnings"):
            st.write(f"**Next earnings:** {mi['next_earnings']}")
        if mi.get("fundamentals"):
            st.json(mi["fundamentals"], expanded=False)
        for n in mi.get("news", []):
            st.markdown(f"- {n['title']}  \n  _{n.get('publisher') or ''} {n.get('date') or ''}_")
        if not mi.get("available") and not mi.get("news"):
            st.caption("No fundamentals or news available from the data source.")

    st.divider()
    st.subheader("Claude briefing")
    if not briefing.available():
        st.info("Add your ANTHROPIC_API_KEY as a Replit Secret to get the written macro + micro + chart briefing.")
        return
    key = f"brief::{ticker}::{t.asof}::{pos.shares}::{pos.cost_basis}"
    if st.button("Generate briefing", type="primary"):
        try:
            st.session_state[key] = st.write_stream(briefing.stream_briefing(an.facts()))
        except Exception as e:
            st.error(f"Briefing failed: {type(e).__name__}: {e}")
    elif key in st.session_state:
        st.markdown(st.session_state[key])

    st.subheader("Ask a follow-up")
    hist_key = f"chat::{ticker}"
    hist = st.session_state.setdefault(hist_key, [])
    for msg in hist:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    q = st.chat_input(f"Ask about {ticker}: e.g. 'where would you rebuy if it drops?'")
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


# ------------------------------------------------------------------ Market
def page_market():
    st.header("Market backdrop")
    mac = load_macro()
    if not mac.get("available"):
        st.error(mac.get("note", "Market data unavailable.")); return
    st.subheader(f"{mac['label']}  (score {mac['score']:+d}, as of {mac['asof']})")
    st.dataframe(pd.DataFrame(mac["components"]).rename(columns=str.title), hide_index=True, use_container_width=True)
    c1, c2 = st.columns(2)
    c1.markdown("**Sector leaders (1m):** " + ", ".join(mac["leaders_1m"]))
    c2.markdown("**Sector laggards (1m):** " + ", ".join(mac["laggards_1m"]))
    sect = pd.DataFrame(mac["sectors"]).T.rename(columns={"name": "Sector", "1m": "1m %", "3m": "3m %", "above_50d": "Above 50d"})
    st.dataframe(sect.sort_values("1m %", ascending=False), use_container_width=True)
    st.caption(mac["note"])


{"Portfolio": page_portfolio, "Stock": page_stock, "Market": page_market, "Holdings": page_holdings}[page]()
