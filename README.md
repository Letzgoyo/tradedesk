# TradeDesk

Personal stock decision-support, built around resource stocks (uranium, gold, silver, copper) as well as ordinary
equities. For each stock you own or watch it tells you the backdrop (the metal and sector, not just the S&P 500),
the company picture, the chart levels on daily **and weekly/monthly** bars, and a position-aware plan (hold, trim,
sell and rebuy lower, add on a pullback) with exact price levels for each branch. It can message your phone when a
level is hit. It is not an auto-trader.

## What's in it
| Area | What it does |
|---|---|
| **Ticker handling** (`symbols.py`) | Type `DML` and it finds the listing Yahoo actually has (`DML.TO`), remembers it, and tells you about other listings (e.g. `DNN`). Shows the currency (CAD/AUD/GBp). Clear error with next steps if nothing matches. |
| **Themes** (`profiles.py`, `macro.py`) | Auto-detects uranium / gold / silver / copper / oil / lithium / tech names (override per holding). Each theme tracks the metal (or proxy), sector ETFs, a bellwether, and dollar/yield headwinds. Resource stocks are scored on their metal first, the S&P at half weight. |
| **What it moves with** | 90-day correlation and beta against the S&P, the metal, the ETFs and the bellwether. |
| **Weekly & monthly** (`technicals.py`) | Same engine run on weekly/monthly bars: major support/resistance, Fib legs, trend. Weekly zones are outlined on the daily chart; the plan flags daily/weekly conflicts. |
| **Volatility scaling** | Stops, minimum stop distance and "stretched" tests scale to each stock's own volatility (low/normal/high/extreme). |
| **Backtest** (`backtest.py`) | Replays the stance through history using only what was known at each date, compares "follow the stance" with buy-and-hold, shows outcomes by stance, and checks stability (first 60% vs last 40%). A test proves it cannot see the future. |
| **Data resilience** (`providers.py`, `data.py`) | Yahoo, then Polygon (optional key, US only), then Stooq. Last good copy is saved to disk and served (flagged stale) if every source is down. |
| **News & events** (`news.py`, `events.py`) | Company and metal headlines (Google News RSS) plus Yahoo news; FOMC and jobs-report dates (approximate: verify), plus your own events in `data/events.json`. |
| **Phone alerts** (`alerts.py`, `notify.py`, `run_alerts.py`) | Stop / rebuy / target hit, price within 1% of one, or stance change, via ntfy, Telegram or Discord. Fires once per level, re-arms after a full ATR. |
| **Claude briefing** (`briefing.py`) | Writes the backdrop + company + chart briefing from the computed facts, answers follow-ups, and cross-checks a chart screenshot. It never invents numbers. |

## Run on Replit
1. Import this repo into Replit (Create Repl, Import from GitHub, `Letzgoyo/tradedesk`).
2. In **Secrets** add `ANTHROPIC_API_KEY` (optional: everything except the written briefing works without it).
3. Press **Run** (`start.sh` starts the alerts loop and the web app). Add your tickers under **Holdings**.
4. For phone alerts, see the **Alerts** tab (ntfy takes two minutes). Free Repls sleep, so for dependable alerts use a
   Replit Deployment, or run `python run_alerts.py` as a Scheduled Deployment.

Locally: `pip install -r requirements.txt && streamlit run app.py`.
CLI: `python cli.py DML --shares 100 --cost 2.10 --chart dml.png --briefing`, `python -m tradedesk.backtest DNN --years 5`.
Tests: `pytest` (synthetic data; `tests/conftest.py` sets `TRADEDESK_DEMO=1`).

## Known limits (read these)
- **Not yet verified against live data.** Everything was built and tested on synthetic prices because the build sandbox
  cannot reach Yahoo. Expect small fixes when it first meets real data. Yahoo is free and unofficial: it can rate-limit or change.
- **Uranium spot isn't on Yahoo.** The Sprott Physical Uranium Trust (`U-UN.TO`) is the proxy. Futures (GC=F, SI=F, HG=F) are front-month.
- **Scoring thresholds are defaults, not tuned.** Use the Backtest tab per stock; a backtest on one stock is a small sample,
  and a good result is not a promise. Backtests on synthetic data mean nothing about real markets.
- Daily bars, delayed; alerts check every 30 minutes, not tick by tick.
- Event dates are approximate. News is headlines only.
- Replit local files can be reset on redeploy. Keep a copy of `data/holdings.json`.
- Analysis only, not personalised financial or tax advice.
