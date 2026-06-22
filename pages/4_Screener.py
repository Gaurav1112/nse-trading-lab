from datetime import datetime, timezone, timedelta

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, ColumnsAutoSizeMode

from components import theme, state
from components.security import SYMBOL_RE, _safe_csv
from nse_backtest.data import fetch_multiple, fetch_nifty50, NIFTY50_SYMBOLS, NIFTY100_SYMBOLS
from nse_backtest.trading_modes import analyze_swing, analyze_positional, analyze_longterm, analyze_intraday
from nse_backtest.sample_data import trending_stock, volatile_midcap, sideways_stock

_ANALYZE_FNS = {"Swing (2-15d)": analyze_swing, "Positional (15-90d)": analyze_positional,
                "Long-Term (90d+)": analyze_longterm, "Intraday": analyze_intraday}
INTRADAY_LABEL = "Intraday"
IST = timezone(timedelta(hours=5, minutes=30))

st.set_page_config(page_title="Screener | Trading Lab", page_icon="📊", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()


# Threaded into analyze_swing (and downstream analyze_stock) so the v2
# engine's rs_vs_nifty + regime_gate actually fire. Without this, swing
# scores on the screener silently fall back to v1 and disagree with Picks.
@st.cache_data(ttl=3600, show_spinner=False)
def _cached_nifty():
    try:
        return fetch_nifty50(start="2022-01-01")
    except Exception:
        return None


# ── Intraday data path ──────────────────────────────────────────────────────
# Previous bug: when the user picked "Intraday" mode, the Screener still
# fetched DAILY bars (start="2021-01-01") and ran analyze_intraday on them.
# That produces stale signals because analyze_intraday reads df["Close"].iloc[-1]
# expecting the LATEST bar — but in daily mode the latest bar is yesterday's
# close (yfinance is EOD). Intraday needs 15-min bars to actually be intraday.
@st.cache_data(ttl=60, show_spinner=False)
def _fetch_15m_batch(symbols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """Single yf.download for the universe at 15-min interval.

    yfinance limits 15m data to the last ~60 days, which is more than enough
    for intraday scoring (we typically use the last 25-100 bars). Returns
    {symbol: df} with normalized OHLCV columns. Empty dict on failure.
    """
    if not symbols:
        return {}
    try:
        import yfinance as yf
        tickers = " ".join(f"{s}.NS" for s in symbols)
        df = yf.download(
            tickers=tickers, period="60d", interval="15m",
            group_by="ticker", auto_adjust=True, progress=False, threads=False,
        )
    except Exception:
        return {}
    if df is None or len(df) == 0:
        return {}
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        full = f"{sym}.NS"
        try:
            if isinstance(df.columns, pd.MultiIndex):
                if full not in df.columns.get_level_values(0):
                    continue
                sub = df[full].dropna()
            else:
                sub = df.dropna()
            if len(sub) >= 25 and "Close" in sub.columns:
                out[sym] = sub
        except Exception:
            continue
    return out


def _market_open_now() -> bool:
    """Indian equity cash market hours: Mon-Fri 09:15-15:30 IST."""
    now = datetime.now(tz=IST)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    hm = (now.hour, now.minute)
    return (9, 15) <= hm <= (15, 30)

st.markdown("# 📊 Stock Screener")
st.markdown("_Scan the market. Click any row to open full analysis._")

sc1, sc2, sc3, sc4 = st.columns([2, 2, 1, 2])
mode = sc1.selectbox("Trading Mode", list(_ANALYZE_FNS.keys()), key="scr_mode")
universe = sc2.selectbox("Universe", ["Nifty 50 (Live)", "Nifty 100 (Live)", "Custom", "Demo"], key="scr_univ")
max_n = sc3.number_input("Max results", 1, 50, 15, key="scr_max")
custom_input = sc4.text_input("Custom symbols (comma-sep)", key="scr_custom", disabled=(universe != "Custom"))

# ── Mode-specific data freshness banner ─────────────────────────────────
is_intraday = (mode == INTRADAY_LABEL)
if is_intraday:
    if _market_open_now():
        st.success(
            "⚡ **Intraday mode — using LIVE 15-min bars** (yfinance, ~15 min lag). "
            "Levels you see are computed from today's actual price action, not yesterday's close."
        )
    else:
        st.warning(
            "🛑 **Market is CLOSED right now** (Mon-Fri 09:15-15:30 IST). "
            "Intraday scan will use the last available 15-min bars (from the previous "
            "trading session) — these are stale for live trading decisions. "
            "Run again once the market opens. For swing/positional analysis switch the "
            "Trading Mode above."
        )
else:
    st.caption(
        "Swing / Positional / Long-Term modes use daily bars (yfinance EOD). "
        "For live intraday signals, switch Trading Mode to **Intraday** — "
        "we'll fetch 15-min bars automatically."
    )

last_scan = st.session_state.get("scr_last_scan")
if last_scan:
    st.caption(f"Last scan: {last_scan}")

if st.button("🔍 Scan", type="primary", use_container_width=True, key="scr_run"):
    # Determine universe symbol list
    if universe == "Custom":
        syms = [s.strip().upper() for s in custom_input.split(",")
                if s.strip() and SYMBOL_RE.match(s.strip().upper())]
    elif universe == "Demo":
        syms = []  # Demo path is special-cased below
    elif "100" in universe:
        syms = NIFTY100_SYMBOLS
    else:
        syms = NIFTY50_SYMBOLS

    # ── Branch by mode ──
    if universe == "Demo":
        # Demo path is always synthetic daily bars regardless of mode.
        raw_data = {"DEMO_UP": trending_stock(), "DEMO_MID": volatile_midcap(),
                    "DEMO_FLAT": sideways_stock(), "DEMO_UP2": trending_stock()}
    elif is_intraday:
        # Live 15-min bars — the actual fix for the user's bug.
        with st.spinner(f"Fetching live 15-min bars for {len(syms)} symbols…"):
            raw_data = _fetch_15m_batch(tuple(syms))
        if not raw_data:
            st.error(
                "No 15-min data returned. Possible causes: market closed and the "
                "60-day yfinance window is empty for these symbols; yfinance rate-limit; "
                "or all symbols are recently-listed (insufficient 15-min history)."
            )
            st.stop()
    else:
        raw_data = fetch_multiple(syms, start="2021-01-01")

    analyze_fn = _ANALYZE_FNS[mode]
    cap, risk = state.get_capital(), state.get_risk_pct()
    # Skip Nifty fetch entirely on Demo (synthetic universe doesn't need it).
    nifty_for_engine = None if universe == "Demo" else _cached_nifty()
    rows = []
    prog = st.progress(0, text="Scanning…")
    total = len(raw_data)
    # Intraday bars are shorter-lived; relax the minimum-history floor.
    min_bars = 25 if is_intraday else 60
    for i, (sym, df) in enumerate(raw_data.items()):
        prog.progress((i + 1) / total, text=f"Scanning {sym}…")
        if len(df) < min_bars:
            continue
        try:
            setup = analyze_fn(df, sym, cap, risk, nifty_df=nifty_for_engine)
            if setup:
                # Why-HOLD: when raw score is ≥65 but signal is HOLD, a downstream
                # gate (regime / MTF / liquidity / gap / earnings) demoted the GO.
                # Surface the most informative reason on the row.
                why_hold = ""
                if setup.signal == "HOLD" and setup.score >= 65:
                    for r in setup.reasons:
                        if any(t in r for t in ("Regime block", "MTF disconfirmation",
                                                "Gap-up too large", "Liquidity too thin",
                                                "Earnings inside")):
                            why_hold = r[:60]
                            break
                    if not why_hold:
                        why_hold = "Score ≥65 but downstream safety gate fired"
                row = {"Symbol": sym, "Signal": setup.signal, "Score": round(setup.score, 0),
                       "Setup": getattr(setup, "strategy_name", mode) or mode,
                       "CMP ₹": round(setup.entry_price, 2 if is_intraday else 0),
                       "SL ₹": round(setup.stop_loss, 2 if is_intraday else 0),
                       "Target ₹": round(setup.target_1, 2 if is_intraday else 0),
                       "R:R": round(setup.risk_reward, 2),
                       "Win %": round(setup.win_probability, 0),
                       "Why HOLD": why_hold}
                # Bar-age surfaced for intraday so user sees data freshness per row
                if is_intraday and hasattr(df.index, "max"):
                    try:
                        last_ts = df.index.max()
                        if hasattr(last_ts, "tz_convert"):
                            last_ts = last_ts.tz_convert(IST)
                        age_min = int(
                            (datetime.now(tz=IST) - last_ts).total_seconds() / 60
                        )
                        row["Bar age (min)"] = age_min
                    except Exception:
                        pass
                rows.append(row)
        except Exception:
            pass
    prog.empty()
    df_res = pd.DataFrame(rows)
    if not df_res.empty:
        df_res = df_res.sort_values("Score", ascending=False).head(int(max_n)).reset_index(drop=True)
    st.session_state["scr_results"] = df_res
    st.session_state["scr_last_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.rerun()

df_results: pd.DataFrame = st.session_state.get("scr_results", pd.DataFrame())

if df_results.empty:
    st.info("Hit **Scan** to discover trading candidates.")
else:
    st.markdown(f"**{len(df_results)} stocks found** — click any row to open full analysis.")
    gb = GridOptionsBuilder.from_dataframe(df_results)
    gb.configure_selection(selection_mode="single", use_checkbox=False)
    gb.configure_column("Signal", cellStyle={"function":
        "params.value==='BUY'?{color:'#10b981',fontWeight:'700'}:params.value==='SELL'?{color:'#ef4444'}:{color:'#f59e0b'}"})
    gb.configure_column("Score", cellStyle={"function":
        "params.value>=65?{color:'#10b981',fontWeight:'700'}:params.value>=45?{color:'#f59e0b'}:{color:'#ef4444'}"})
    gb.configure_column("R:R", cellStyle={"function":
        "params.value>=2?{color:'#10b981'}:params.value>=1.5?{color:'#f59e0b'}:{color:'#ef4444'}"})
    gb.configure_grid_options(domLayout="autoHeight", rowStyle={"background": "#111827"})
    grid_opts = gb.build()
    grid_response = AgGrid(df_results, gridOptions=grid_opts,
                            update_mode=GridUpdateMode.SELECTION_CHANGED,
                            columns_auto_size_mode=ColumnsAutoSizeMode.FIT_CONTENTS,
                            theme="alpine",
                            height=min(400, 80 + len(df_results) * 40),
                            allow_unsafe_jscode=True)
    selected = grid_response.get("selected_rows", [])
    if selected is not None and len(selected) > 0:
        sym_sel = selected[0]["Symbol"] if isinstance(selected[0], dict) else selected.iloc[0]["Symbol"]
        state.set_analyze_sym(str(sym_sel))
        st.session_state["auto_analyze"] = True
        st.switch_page("pages/3_Analyze.py")

    st.download_button("📥 Download CSV", data=_safe_csv(df_results),
                        file_name=f"scan_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                        mime="text/csv")
