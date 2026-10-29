from __future__ import annotations
import os
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from fyers_apiv3 import fyersModel
import yfinance as yf

_log = logging.getLogger(__name__)


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


def fetch_yfinance_ltp(symbols: list[str]) -> dict[str, LTPQuote]:
    """Fetch last close from yfinance 1-min bars (delayed ~15 min).

    Never raises — logs and returns partial dict on failure.
    """
    out: dict[str, LTPQuote] = {}
    for sym in symbols:
        try:
            df = yf.download(f"{sym}.NS", period="1d", interval="1m", progress=False, auto_adjust=False)
            if df is None or df.empty:
                continue
            last = df.tail(1).iloc[0]
            ts = last.name.to_pydatetime() if hasattr(last.name, "to_pydatetime") else datetime.now(timezone.utc)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out[sym] = LTPQuote(symbol=sym, ltp=float(last["Close"]), ts=ts, source="yfinance")
        except Exception as e:  # yfinance is flaky by design
            _log.warning("yfinance ltp failed for %s: %s", sym, e)
    return out


def fetch_ltp_with_fallback(symbols: list[str]) -> tuple[dict[str, LTPQuote], str]:
    """Primary: Fyers. Fallback: yfinance. Returns (quotes, source_label).

    source_label: "fyers" | "yfinance" | "mixed" (used by trust badge).
    """
    try:
        fy = fetch_fyers_ltp(symbols)
        if fy and len(fy) >= len(symbols) * 0.9:
            return fy, "fyers"
        missing = [s for s in symbols if s not in fy]
        if missing:
            yf_fill = fetch_yfinance_ltp(missing)
            merged = {**fy, **yf_fill}
            return merged, "mixed" if fy else "yfinance"
        return fy, "fyers"
    except FyersAuthError as e:
        _log.warning("Fyers auth failed, falling back to yfinance: %s", e)
        return fetch_yfinance_ltp(symbols), "yfinance"
