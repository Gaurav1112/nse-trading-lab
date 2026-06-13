"""Nifty 50 sector classification.

Used by the sector-exposure cap in risk_governor — refuses to open a new
position if the user already holds the cap in that sector. The cap is a
correlated-drawdown guard: 5 banks all sell off together, etc.

Classification matches NSE's sectoral indices broadly (^NSEBANK,
^CNXIT, ^CNXAUTO, etc.) but flattens some sub-sectors. When a symbol
is unknown, sector_of returns "Unclassified" and the cap defaults to
unlimited (no block) so the engine doesn't refuse new listings.
"""
from __future__ import annotations


_SECTOR_OF: dict[str, str] = {
    # Banks
    "HDFCBANK": "Banks", "ICICIBANK": "Banks", "SBIN": "Banks",
    "KOTAKBANK": "Banks", "AXISBANK": "Banks", "INDUSINDBK": "Banks",
    # Financial Services / NBFC / Insurance
    "BAJFINANCE": "Financials", "BAJAJFINSV": "Financials",
    "SHRIRAMFIN": "Financials", "HDFCLIFE": "Financials",
    "SBILIFE": "Financials", "JIOFIN": "Financials",
    # IT
    "TCS": "IT", "INFY": "IT", "HCLTECH": "IT", "TECHM": "IT",
    "WIPRO": "IT",
    # Auto + Auto-anc
    "MARUTI": "Auto", "M&M": "Auto", "EICHERMOT": "Auto",
    "HEROMOTOCO": "Auto", "BAJAJ-AUTO": "Auto", "TMPV": "Auto",
    # Energy / Oil & Gas / Power
    "RELIANCE": "Energy", "ONGC": "Energy", "BPCL": "Energy",
    "COALINDIA": "Energy", "NTPC": "Energy", "POWERGRID": "Energy",
    # Pharma + Health
    "SUNPHARMA": "Pharma", "DIVISLAB": "Pharma", "CIPLA": "Pharma",
    "DRREDDY": "Pharma", "APOLLOHOSP": "Pharma",
    # FMCG
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG", "TATACONSUM": "FMCG",
    # Metals
    "JSWSTEEL": "Metals", "TATASTEEL": "Metals", "HINDALCO": "Metals",
    # Cement / Construction Materials
    "ULTRACEMCO": "Cement", "GRASIM": "Cement",
    # Telecom
    "BHARTIARTL": "Telecom",
    # Retail / Discretionary
    "DMART": "Retail", "TRENT": "Retail", "TITAN": "Retail",
    "ASIANPAINT": "Retail",
    # Infra / Capital Goods
    "LT": "Infra",
    # Adani Group (separate to surface correlation risk)
    "ADANIENT": "Adani", "ADANIPORTS": "Adani",
}


def sector_of(symbol: str) -> str:
    """Return the sector tag for symbol, or 'Unclassified'.

    Accepts both 'RELIANCE' and 'RELIANCE.NS'.
    """
    if not symbol:
        return "Unclassified"
    sym = symbol.replace(".NS", "").replace(".BO", "").upper()
    return _SECTOR_OF.get(sym, "Unclassified")
