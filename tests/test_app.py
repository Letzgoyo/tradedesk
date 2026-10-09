from pathlib import Path

from streamlit.testing.v1 import AppTest

from tradedesk import config, portfolio
from tradedesk.portfolio import Position


def run(page, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "HOLDINGS_FILE", tmp_path / "holdings.json")
    portfolio.save([Position("NVDA", 20, 400, "position"), Position("AAPL", 0, 0)])
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=120)
    at.session_state["page"] = page
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_every_page_renders(tmp_path, monkeypatch):
    for page in ("Portfolio", "Stock", "Themes", "Market", "Backtest", "Alerts", "Holdings"):
        run(page, tmp_path, monkeypatch)
