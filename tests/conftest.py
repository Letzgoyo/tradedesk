import os
import sys
from pathlib import Path

os.environ["TRADEDESK_DEMO"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from tradedesk import config  # noqa: E402


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """Point all file storage (holdings, cache, alert state) at a temp dir."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "HOLDINGS_FILE", tmp_path / "holdings.json")
    return tmp_path
