"""Upcoming market-moving events. The FOMC list and the first-Friday jobs report are built in and
APPROXIMATE: verify on federalreserve.gov / bls.gov. Add your own in data/events.json as
[{"date": "2026-11-12", "event": "US CPI", "note": ""}]."""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import config

FOMC_DECISION_DAYS = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29",
                      "2026-09-16", "2026-10-28", "2026-12-09"]


def _first_friday(y: int, m: int) -> date:
    d = date(y, m, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def upcoming(days: int = 21, today: date | None = None) -> list[dict]:
    today = today or date.today()
    end = today + timedelta(days=days)
    ev = [{"date": d, "event": "FOMC rate decision", "note": "verify date"} for d in FOMC_DECISION_DAYS]
    for k in range(0, 3):
        y, m = (today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12 + 1)
        ev.append({"date": _first_friday(y, m).isoformat(), "event": "US jobs report (approx.)", "note": "usually first Friday"})
    try:
        ev += json.loads((config.DATA_DIR / "events.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    out = [e for e in ev if today.isoformat() <= str(e.get("date", "")) <= end.isoformat()]
    return sorted(out, key=lambda e: e["date"])
