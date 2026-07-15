from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from fyers_apiv3 import fyersModel


class FyersAuthError(RuntimeError):
    """Raised when Fyers API rejects credentials (401/403/invalid token)."""


@dataclass(frozen=True)
class LTPQuote:
    symbol: str
    ltp: float
    ts: datetime
    source: str  # "fyers" | "yfinance" | "nse"


def _fyers_client() -> fyersModel.FyersModel:
    app_id = os.environ["FYERS_APP_ID"]
    token = os.environ["FYERS_ACCESS_TOKEN"]
    return fyersModel.FyersModel(client_id=app_id, token=token, is_async=False)


def _to_fyers_symbol(sym: str) -> str:
    """Map raw NSE ticker to Fyers-namespaced form."""
    return f"NSE:{sym}-EQ"


def _from_fyers_symbol(fs: str) -> str:
    return fs.replace("NSE:", "").replace("-EQ", "")


def fetch_fyers_ltp(symbols: list[str]) -> dict[str, LTPQuote]:
    """Fetch last-traded price for a batch of NSE symbols via Fyers.

    Raises FyersAuthError on credential failure so caller can fall back to yfinance.
    Returns partial dict on partial success (never raises for missing symbols).
    """
    client = _fyers_client()
    fyers_syms = [_to_fyers_symbol(s) for s in symbols]
    response = client.quotes({"symbols": ",".join(fyers_syms)})
    if response.get("s") != "ok":
        code = response.get("code")
        msg = response.get("message", "")
        if code in (-300, -352) or "token" in msg.lower():
            raise FyersAuthError(msg)
        return {}
    out: dict[str, LTPQuote] = {}
    for entry in response.get("d", []):
        sym = _from_fyers_symbol(entry["n"])
        v = entry["v"]
        out[sym] = LTPQuote(
            symbol=sym,
            ltp=float(v["lp"]),
            ts=datetime.fromtimestamp(int(v["tt"]), tz=timezone.utc),
            source="fyers",
        )
    return out
