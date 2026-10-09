"""Themes: which instruments actually drive a stock. A uranium miner follows uranium and uranium
ETFs, not the S&P 500. Each theme names the metal/commodity proxy, the sector ETFs, a bellwether
stock, and anything that moves inversely (dollar, yields)."""
from __future__ import annotations

THEMES: dict[str, dict] = {
    "uranium": {"label": "Uranium", "metal": "U-UN.TO", "metal_label": "Sprott Physical Uranium Trust (U.UN, uranium spot proxy)",
                "etfs": ["URA", "URNM"], "leader": "CCJ", "inverse": []},
    "gold": {"label": "Gold", "metal": "GC=F", "metal_label": "Gold futures",
             "etfs": ["GDX", "GDXJ"], "leader": "NEM", "inverse": ["DX-Y.NYB", "^TNX"]},
    "silver": {"label": "Silver", "metal": "SI=F", "metal_label": "Silver futures",
               "etfs": ["SIL", "SILJ"], "leader": "PAAS", "inverse": ["DX-Y.NYB"], "also": ["GC=F"]},
    "copper": {"label": "Copper", "metal": "HG=F", "metal_label": "Copper futures",
               "etfs": ["COPX"], "leader": "FCX", "inverse": ["DX-Y.NYB"], "also": ["FXI"]},
    "energy": {"label": "Oil & gas", "metal": "CL=F", "metal_label": "WTI crude futures",
               "etfs": ["XLE", "XOP"], "leader": "XOM", "inverse": []},
    "lithium": {"label": "Lithium / battery metals", "metal": None, "metal_label": None,
                "etfs": ["LIT"], "leader": "ALB", "inverse": []},
    "tech": {"label": "Technology", "metal": None, "metal_label": None, "etfs": ["QQQ"], "leader": None, "inverse": []},
    "broad": {"label": "Broad market", "metal": None, "metal_label": None, "etfs": ["SPY"], "leader": None, "inverse": []},
}
THEME_CHOICES = ["auto"] + list(THEMES)
RESOURCE_THEMES = {"uranium", "gold", "silver", "copper", "energy", "lithium"}

_KNOWN = {
    "uranium": "UROY DML DNN CCJ NXE UEC UUUU URG EU BOE PDN DYL LOT BMN PEN URA URNM SRUUF U-UN",
    "gold": "BTO ELE NEM GOLD AEM KGC AU FNV WPM EGO BTG NST EVN RRL PRU SBM GOR HMY AGI OR EQX GDX GDXJ GLD",
    "silver": "VZLA PSIL PAAS AG HL CDE SVM FSM MAG EXK SIL SILJ SLV",
    "copper": "IE VCU FCX SCCO TECK COPX HBM ERO IVN SFR CPER",
    "energy": "XOM CVX OXY COP XLE XOP STO WDS",
    "lithium": "ALB SQM LAC PLS LIT PLL",
}
KNOWN = {sym: theme for theme, syms in _KNOWN.items() for sym in syms.split()}

_INDUSTRY = (("uranium", "uranium"), ("gold", "gold"), ("silver", "silver"), ("copper", "copper"),
             ("oil & gas", "energy"), ("lithium", "lithium"))
_NAME = (("uranium", "uranium"), ("gold", "gold"), ("silver", "silver"), ("copper", "copper"),
         ("lithium", "lithium"), ("energy", "energy"), ("petroleum", "energy"))


def base_symbol(symbol: str) -> str:
    return symbol.upper().split(".")[0]


def detect_theme(symbol: str, micro: dict | None = None, override: str = "auto") -> tuple[str, str]:
    """Returns (theme, how). User override > known ticker list > Yahoo industry > company name > sector."""
    if override and override != "auto" and override in THEMES:
        return override, "set by you"
    base = base_symbol(symbol)
    if base in KNOWN:
        return KNOWN[base], "known ticker"
    f = (micro or {}).get("fundamentals") or {}
    industry = (f.get("industry") or "").lower()
    for key, theme in _INDUSTRY:
        if key in industry:
            return theme, f"Yahoo industry: {f.get('industry')}"
    name = (f.get("shortName") or "").lower()
    for key, theme in _NAME:
        if key in name:
            return theme, f"company name contains '{key}'"
    if f.get("sector") == "Technology":
        return "tech", "Yahoo sector: Technology"
    return "broad", "default"


def instruments(theme: str) -> list[tuple[str, str]]:
    """(symbol, role) for everything we track for a theme."""
    t = THEMES.get(theme, THEMES["broad"])
    out: list[tuple[str, str]] = []
    if t.get("metal"):
        out.append((t["metal"], "metal"))
    out += [(s, "sector ETF") for s in t["etfs"]]
    if t.get("leader"):
        out.append((t["leader"], "bellwether stock"))
    out += [(s, "inverse driver") for s in t.get("inverse", [])]
    out += [(s, "related") for s in t.get("also", [])]
    return out
