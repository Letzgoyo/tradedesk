"""Quick command-line use:  python cli.py NVDA --shares 20 --cost 110 --chart nvda.png [--briefing]"""
import argparse
import json
import sys

from tradedesk import analysis, briefing, charts, macro
from tradedesk.portfolio import Position


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--shares", type=float, default=0)
    ap.add_argument("--cost", type=float, default=0)
    ap.add_argument("--horizon", default="position", choices=["swing", "position", "long_term"])
    ap.add_argument("--chart", help="write annotated chart PNG here")
    ap.add_argument("--briefing", action="store_true", help="also stream the Claude briefing")
    ap.add_argument("--json", action="store_true", help="dump all facts as JSON")
    a = ap.parse_args()

    mac = macro.macro_regime()
    an = analysis.analyze_ticker(a.ticker, mac, Position(a.ticker.upper(), a.shares, a.cost, a.horizon))
    if a.json:
        print(json.dumps(an.facts(), indent=1, default=str))
        return 0
    p = an.plan
    print(f"\n{an.ticker} @ {an.tech.price:.2f} (as of {an.tech.asof}, data: {an.source})")
    print(f"Macro: {mac['label']} | Trend: {an.tech.trend['label']} | RSI {an.tech.rsi:.0f}")
    print(f"\n>>> {p['stance']}: {p['headline']}  (score {p['score']:+d})")
    for f in p["factors"]:
        print(f"   {f['score']:+d}  {f['factor']}: {f['why']}")
    print("\nPlan:")
    for r in p["rules"]:
        print("  -", r)
    print("\nAlerts:")
    for al in p["alerts"]:
        print(f"  {al['direction']:>5} {al['price']:>9.2f} ({al['dist_pct']:+.1f}%)  {al['why']}")
    if a.chart:
        charts.render(an).savefig(a.chart, dpi=130)
        print("\nchart ->", a.chart)
    if a.briefing:
        if not briefing.available():
            print("\nSet ANTHROPIC_API_KEY to enable the briefing.", file=sys.stderr)
            return 1
        print("\n" + "=" * 70)
        for chunk in briefing.stream_briefing(an.facts()):
            print(chunk, end="", flush=True)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
