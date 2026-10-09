"""Turn the plan's price levels into notifications, without spamming.

Fires on: a level being crossed (stop, pullback/rebuy level, target/trim level), price coming
within ~1% of one, and a change of stance. Each alert fires once, then re-arms only after price
moves a full ATR back the other way. State lives in data/alert_state.json."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import config

STATE_FILE = lambda: config.DATA_DIR / "alert_state.json"
LOG_FILE = lambda: config.DATA_DIR / "alert_log.json"
APPROACH_PCT = 1.0
COOLDOWN_HOURS = 20


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"levels": {}, "stance": {}}


def save_state(state: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE().write_text(json.dumps(state, indent=1))


def load_log() -> list[dict]:
    try:
        return json.loads(LOG_FILE().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def append_log(events: list[dict]) -> None:
    if not events:
        return
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE().write_text(json.dumps((load_log() + events)[-300:], indent=1))


def _hours_since(iso: str, now: datetime) -> float:
    return (now - datetime.fromisoformat(iso)).total_seconds() / 3600


def evaluate(an, state: dict, now: datetime | None = None) -> list[dict]:
    """Returns new events for one Analysis and updates `state` in place."""
    now = now or datetime.now(timezone.utc)
    t, pl = an.ticker, an.plan
    price, atr = an.tech.price, an.tech.atr
    events: list[dict] = []

    prev = state["stance"].get(t)
    if prev and prev != pl["stance"]:
        events.append({"ticker": t, "kind": "stance", "urgent": pl["stance"].startswith(("EXIT", "REDUCE", "TRIM")),
                       "title": f"{t}: stance changed",
                       "body": f"{prev} -> {pl['stance']}. {pl['headline']} (price {price:.2f})"})
    state["stance"][t] = pl["stance"]

    for al in pl["alerts"]:
        lvl, direction = al["price"], al["direction"]
        key = f"{t}|{direction}|{lvl:.1f}" if lvl > 20 else f"{t}|{direction}|{lvl:.2f}"
        st = state["levels"].setdefault(key, {"armed": True, "last": None})
        crossed = price <= lvl if direction == "below" else price >= lvl
        dist = abs(price / lvl - 1) * 100
        away = (price > lvl + atr) if direction == "below" else (price < lvl - atr)
        if away:
            st["armed"] = True
        if not st["armed"]:
            continue
        if st["last"] and _hours_since(st["last"], now) < COOLDOWN_HOURS:
            continue
        if crossed:
            verb = "fell to" if direction == "below" else "rose to"
            events.append({"ticker": t, "kind": "cross", "urgent": al["why"].startswith("invalidation"),
                           "title": f"{t}: {al['why'].split(':')[0]} hit",
                           "body": f"{t} {verb} {price:.2f}, through {lvl:.2f} ({al['why']}). Plan: {pl['stance']}."})
            st.update(armed=False, last=now.isoformat())
        elif dist <= APPROACH_PCT:
            events.append({"ticker": t, "kind": "approach", "urgent": False,
                           "title": f"{t}: approaching {lvl:.2f}",
                           "body": f"{t} at {price:.2f} is {dist:.1f}% from {lvl:.2f} ({al['why']})."})
            st.update(last=now.isoformat())
    for e in events:
        e["time"] = now.isoformat()
    return events


def digest(analyses: list) -> tuple[str, str]:
    lines = []
    for an in analyses:
        p, t = an.plan, an.tech
        lines.append(f"{an.ticker} {t.price:.2f} ({t.chg_1d:+.1f}%): {p['stance']}; stop {p['levels']['stop']}")
    return f"TradeDesk daily digest ({datetime.now().strftime('%d %b')})", "\n".join(lines) or "No holdings."
