import yfinance as yf
import streamlit as st

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


@st.cache_data(ttl=300, show_spinner=False)
def get_market_breadth(symbols: tuple[str, ...]) -> dict[str, int | float]:
    above = total = 0
    for sym in symbols:
        try:
            df = yf.Ticker(f"{sym}.NS").history(period="3mo")
            if len(df) >= 20:
                ema20 = df["Close"].ewm(span=20, adjust=False).mean().iloc[-1]
                if df["Close"].iloc[-1] > ema20:
                    above += 1
                total += 1
        except Exception:
            pass
    return {"above": above, "total": total, "pct": round(above / total * 100, 1) if total else 0.0}


@st.cache_data(ttl=300, show_spinner=False)
def get_top_movers(symbols: tuple[str, ...], n: int = 5) -> dict[str, list[dict]]:
    movers = []
    for sym in symbols:
        try:
            hist = yf.Ticker(f"{sym}.NS").history(period="2d")
            if len(hist) >= 2:
                last, prev = float(hist["Close"].iloc[-1]), float(hist["Close"].iloc[-2])
                movers.append({"symbol": sym, "price": last, "change_pct": (last - prev) / prev * 100})
        except Exception:
            pass
    movers.sort(key=lambda x: x["change_pct"], reverse=True)
    return {"gainers": movers[:n], "losers": movers[-n:][::-1]}
