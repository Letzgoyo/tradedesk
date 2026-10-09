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


def _read(path) -> list[Position]:
    raw = json.loads(path.read_text())
    return [Position(**{k: v for k, v in r.items() if k in Position.__dataclass_fields__}) for r in raw]


def load() -> list[Position]:
    """Your saved holdings; until you save any, the starter list in holdings.seed.json (if present)."""
    try:
        return _read(config.HOLDINGS_FILE)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    try:
        return _read(config.SEED_FILE)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def has_seed() -> bool:
    return config.SEED_FILE.exists()


def save(positions: list[Position]) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    clean = [asdict(p) for p in positions if p.ticker.strip()]
    for r in clean:
        r["ticker"] = r["ticker"].strip().upper()
    config.HOLDINGS_FILE.write_text(json.dumps(clean, indent=2))
