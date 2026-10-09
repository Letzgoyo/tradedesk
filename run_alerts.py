"""Check every holding against its plan levels and send notifications.

  python run_alerts.py              one pass (use this as a Replit Scheduled Deployment)
  python run_alerts.py --loop 30    keep running, one pass every 30 minutes
  python run_alerts.py --digest     send a one-message summary of every holding
  python run_alerts.py --test       send a test notification
"""
import argparse
import sys
import time

from tradedesk import alerts, analysis, data, macro, notify, portfolio


def one_pass(send_digest: bool = False) -> int:
    positions = portfolio.load()
    if not positions:
        print("No holdings saved yet.")
        return 0
    data._cache.clear()  # always look at fresh prices
    mac = macro.macro_regime()
    state, done, events = alerts.load_state(), [], []
    for p in positions:
        try:
            an = analysis.analyze_ticker(p.ticker, mac, p, with_micro=False, theme_override=p.theme)
        except Exception as exc:
            print(f"{p.ticker}: skipped ({exc})")
            continue
        done.append(an)
        events += alerts.evaluate(an, state)
    alerts.save_state(state)
    alerts.append_log(events)
    for e in events:
        res = notify.send(e["title"], e["body"], e["urgent"])
        print(f"{e['title']}: {e['body']}  -> {res or 'no channel configured'}")
    if send_digest:
        title, body = alerts.digest(done)
        print(notify.send(title, body) or "no channel configured")
    print(f"checked {len(done)} holdings, {len(events)} alerts")
    return len(events)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=float, metavar="MINUTES")
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    if a.test:
        print(notify.send("TradeDesk test", "If you can read this, alerts are working."))
        return 0
    if not notify.configured():
        print("No notification channel configured (set NTFY_TOPIC, or Telegram/Discord). Nothing to do.")
        return 0
    if a.loop:
        while True:
            try:
                one_pass()
            except Exception as exc:  # never let the loop die
                print("pass failed:", exc)
            time.sleep(a.loop * 60)
    one_pass(a.digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
