# TradeDesk

Personal stock decision-support: for each stock you own or watch it tells you the macro backdrop,
the company picture, the chart levels, and a position-aware plan (hold, trim, sell and rebuy lower,
add on a pullback), with the exact price levels for each branch. It is not an auto-trader.

## How it works
1. **Engine (`tradedesk/technicals.py`)**: computes swings (ZigZag), support/resistance zones, Fibonacci
   retracements/extensions with zone confluence, trend structure, RSI/MACD/ATR, volume, double tops/bottoms,
   breakouts/breakdowns, RSI divergence. Levels come from price data, not from reading a picture.
2. **Macro (`macro.py`)**: S&P trend, VIX, 10y yield, dollar, credit spread proxy, sector breadth/rotation -> regime score.
3. **Plan (`plan.py`)**: transparent scoring (trend, momentum, macro, relative strength, reward/risk) plus your
   cost basis, size and holding style -> stance, stop, targets, pullback/rebuy levels, alert prices.
4. **Claude (`briefing.py`)**: writes the macro + micro + chart briefing from those computed facts, answers
   follow-ups, and cross-checks a screenshot of any chart against the computed levels.

## Run on Replit
1. Create a new **Python** Repl and upload the contents of this `tradedesk/` folder to its root
   (so `app.py` and `.replit` are at the top level).
2. In **Secrets**, add `ANTHROPIC_API_KEY` (optional: the app works without it, you just lose the written briefing).
3. Press **Run**. In **Holdings** add your tickers (shares = 0 for a watchlist name), then use **Portfolio**.

Locally: `pip install -r requirements.txt && streamlit run app.py`.
CLI: `python cli.py NVDA --shares 20 --cost 110 --chart nvda.png --briefing`.
Tests: `pytest` (they run on synthetic data; set `TRADEDESK_DEMO=1`, which `tests/conftest.py` does for you).

## Known limits (read these)
- Data is **daily bars from Yahoo via `yfinance`**: free, delayed, unofficial, and it can rate-limit or break.
  No intraday. The data layer is isolated in `data.py` so a paid provider (Polygon, Alpaca, Tiingo) is a one-file swap.
- News is headlines only; there is **no economic calendar** (FOMC/CPI/jobs): check it yourself.
- The scoring thresholds are sensible defaults, **not tuned or backtested**. Treat the stance as a structured
  second opinion. A backtest harness is the natural next step.
- Replit local files can be reset on redeploy. Keep a copy of `data/holdings.json`.
- Analysis only, not personalised financial or tax advice.
