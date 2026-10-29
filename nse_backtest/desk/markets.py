"""Market profiles (NSE, US) and trading universes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from nse_backtest.data import NIFTY50_SYMBOLS


@dataclass(frozen=True)
class MarketProfile:
    code: str
    currency: str
    tz: str
    session_open: time
    session_close: time
    no_entry_before: time
    no_entry_after: time
    flat_by: time
    regime_index: str
    exchange: str  # "NS" for NSE, "" for US (fetch_nse exchange arg)
    min_turnover: float  # local currency, 20d median Close*Volume
    has_ban_list: bool

    def yf_symbol(self, symbol: str) -> str:
        s = symbol.strip().upper()
        if s.startswith("^") or not self.exchange or s.endswith("." + self.exchange):
            return s
        return f"{s}.{self.exchange}"

    def money(self, x: float) -> str:
        return f"{'-' if x < 0 else ''}{self.currency}{abs(x):,.2f}"

    def money_compact(self, x: float) -> str:
        """Large amounts the way each market reads them: crore for NSE, millions for US."""
        sign = "-" if x < 0 else ""
        v = abs(x)
        if self.code == "NSE":
            return f"{sign}{self.currency}{v / 1e7:,.1f} Cr"
        return f"{sign}{self.currency}{v / 1e6:,.1f}M"


NSE = MarketProfile("NSE", "₹", "Asia/Kolkata", time(9, 15), time(15, 30), time(9, 30),
                    time(14, 30), time(15, 15), "^NSEI", "NS", 5e7, True)
US = MarketProfile("US", "$", "America/New_York", time(9, 30), time(16, 0), time(9, 45),
                   time(15, 0), time(15, 55), "^GSPC", "", 2e7, False)

_PROFILES = {"NSE": NSE, "US": US}


def get_profile(code: str) -> MarketProfile:
    try:
        return _PROFILES[code.strip().upper()]
    except (KeyError, AttributeError):
        raise ValueError(f"unknown market {code!r}; expected one of {sorted(_PROFILES)}") from None


US_LARGE_CAP_50: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK-B", "AVGO", "TSLA", "LLY",
    "JPM", "V", "UNH", "XOM", "MA", "COST", "PG", "JNJ", "HD", "ORCL",
    "NFLX", "ABBV", "BAC", "CRM", "KO", "CVX", "WMT", "AMD", "PEP", "TMO",
    "ADBE", "CSCO", "MCD", "ACN", "LIN", "ABT", "WFC", "DIS", "INTU", "QCOM",
    "TXN", "CAT", "IBM", "GE", "AMGN", "VZ", "PFE", "NOW", "UBER", "GS",
]


def universe(profile: MarketProfile) -> list[str]:
    if profile.code == "NSE":
        return list(NIFTY50_SYMBOLS)
    return list(US_LARGE_CAP_50)
