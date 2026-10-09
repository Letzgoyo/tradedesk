from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from . import config


@dataclass
class Position:
    ticker: str
    shares: float = 0.0
    cost_basis: float = 0.0
    horizon: str = "position"  # swing | position | long_term
    notes: str = ""
    theme: str = "auto"  # auto | uranium | gold | silver | copper | ... (see profiles.THEMES)

    @property
    def held(self) -> bool:
        return self.shares > 0


HORIZONS = ("swing", "position", "long_term")


def load() -> list[Position]:
    try:
        raw = json.loads(config.HOLDINGS_FILE.read_text())
        return [Position(**{k: v for k, v in r.items() if k in Position.__dataclass_fields__}) for r in raw]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save(positions: list[Position]) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    clean = [asdict(p) for p in positions if p.ticker.strip()]
    for r in clean:
        r["ticker"] = r["ticker"].strip().upper()
    config.HOLDINGS_FILE.write_text(json.dumps(clean, indent=2))
