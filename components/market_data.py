import yfinance as yf
import streamlit as st


@st.cache_data(ttl=60, show_spinner=False)
def get_live_price(sym: str) -> tuple[float | None, float | None]:
    """Return (last_price, prev_close) for an NSE cash equity symbol.

    Primary: NSE's open /api/quote-equity endpoint (no auth, accurate, but
    rate-limited from cloud egress IPs).
    Fallback: yfinance fast_info (lagged ~1-15 min, occasionally returns
    split-adjusted bleed-through that diverges from the actual market price).
    """
    # Try NSE first
    try:
        from components.nse_live_quote import get_nse_quote
        last, prev = get_nse_quote(sym)
        if last is not None and last > 0:
            return last, (prev if (prev is not None and prev > 0) else None)
    except Exception:
        pass
    # Fall back to yfinance fast_info
    try:
        fi = yf.Ticker(f"{sym}.NS").fast_info
        last = getattr(fi, "last_price", None)
        prev = getattr(fi, "previous_close", None)
        last = float(last) if last and float(last) > 0 else None
        prev = float(prev) if prev and float(prev) > 0 else None
        return last, prev
    except Exception:
        return None, None

NSE_SECTOR_INDICES: dict[str, str] = {
    "IT": "^CNXIT", "Bank": "^NSEBANK", "Auto": "^CNXAUTO",
    "FMCG": "^CNXFMCG", "Pharma": "^CNXPHARMA", "Metal": "^CNXMETAL",
    "Realty": "^CNXREALTY", "Energy": "^CNXENERGY", "Infra": "^CNXINFRA",
    "Media": "^CNXMEDIA", "PSU Bank": "^CNXPSUBANK", "Fin Services": "^CNXFIN",
}
_INDEX_SYMBOLS = {"Nifty 50": "^NSEI", "Nifty Bank": "^NSEBANK", "India VIX": "^INDIAVIX"}


@st.cache_data(ttl=300, show_spinner=False)
def get_indices() -> dict[str, dict]:
    result: dict[str, dict] = {}
    for name, sym in _INDEX_SYMBOLS.items():
        try:
            hist = yf.Ticker(sym).history(period="2d")
            if len(hist) >= 2:
                last, prev = float(hist["Close"].iloc[-1]), float(hist["Close"].iloc[-2])
                result[name] = {"price": last, "change_pct": (last - prev) / prev * 100, "symbol": sym}
            elif len(hist) == 1:
                result[name] = {"price": float(hist["Close"].iloc[-1]), "change_pct": 0.0, "symbol": sym}
            else:
                result[name] = {"price": None, "change_pct": None, "symbol": sym}
        except Exception:
            result[name] = {"price": None, "change_pct": None, "symbol": sym}
    return result


@st.cache_data(ttl=300, show_spinner=False)
def get_sector_performance() -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for name, sym in NSE_SECTOR_INDICES.items():
        try:
            hist = yf.Ticker(sym).history(period="2d")
            if len(hist) >= 2:
                result[name] = (float(hist["Close"].iloc[-1]) - float(hist["Close"].iloc[-2])) / float(hist["Close"].iloc[-2]) * 100
            else:
                result[name] = None
        except Exception:
            result[name] = None
    return result


def _batch_download(symbols: tuple[str, ...], period: str):
    """One yf.download call with all tickers instead of N sequential history() calls.
    Critical on Streamlit Cloud where shared egress IPs hit yfinance rate limits
    fast — 200 sequential requests reliably trigger 429s and blank UI tiles.
    """
    tickers = " ".join(f"{s}.NS" for s in symbols)
    try:
        return yf.download(
            tickers=tickers, period=period, interval="1d",
            group_by="ticker", auto_adjust=True, progress=False, threads=False,
        )
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def get_market_breadth(symbols: tuple[str, ...]) -> dict[str, int | float]:
    data = _batch_download(symbols, period="3mo")
    above = total = 0
    if data is not None and len(data) > 0:
        for sym in symbols:
            try:
                df = data[f"{sym}.NS"].dropna() if len(symbols) > 1 else data.dropna()
                if len(df) >= 20:
                    ema20 = df["Close"].ewm(span=20, adjust=False).mean().iloc[-1]
                    if df["Close"].iloc[-1] > ema20:
                        above += 1
                    total += 1
            except (KeyError, IndexError, ValueError):
                pass
    return {"above": above, "total": total,
            "pct": round(above / total * 100, 1) if total else 0.0}


@st.cache_data(ttl=300, show_spinner=False)
def get_top_movers(symbols: tuple[str, ...], n: int = 5) -> dict[str, list[dict]]:
    data = _batch_download(symbols, period="5d")
    movers = []
    if data is not None and len(data) > 0:
        for sym in symbols:
            try:
                df = data[f"{sym}.NS"].dropna() if len(symbols) > 1 else data.dropna()
                if len(df) >= 2:
                    last = float(df["Close"].iloc[-1])
                    prev = float(df["Close"].iloc[-2])
                    movers.append({"symbol": sym, "price": last,
                                   "change_pct": (last - prev) / prev * 100})
            except (KeyError, IndexError, ValueError):
                pass
    movers.sort(key=lambda x: x["change_pct"], reverse=True)
    return {"gainers": movers[:n], "losers": movers[-n:][::-1]}
