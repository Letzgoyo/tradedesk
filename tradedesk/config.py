import os
from pathlib import Path

MODEL = os.getenv("TRADEDESK_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("TRADEDESK_EFFORT", "medium")
DEMO = os.getenv("TRADEDESK_DEMO", "0") == "1"
DATA_DIR = Path(os.getenv("TRADEDESK_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
HOLDINGS_FILE = DATA_DIR / "holdings.json"
SEED_FILE = Path(__file__).resolve().parent.parent / "holdings.seed.json"
PRICE_TTL_SECONDS = 600

SECTOR_ETF = {
    "Technology": "XLK", "Financial Services": "XLF", "Healthcare": "XLV",
    "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP", "Energy": "XLE",
    "Industrials": "XLI", "Utilities": "XLU", "Real Estate": "XLRE",
    "Basic Materials": "XLB", "Communication Services": "XLC",
}
