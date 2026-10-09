import os
import sys
from pathlib import Path

os.environ["TRADEDESK_DEMO"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
