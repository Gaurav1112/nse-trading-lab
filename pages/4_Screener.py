import streamlit as st
import pandas as pd
from datetime import datetime
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, ColumnsAutoSizeMode
from components import theme, state
from components.security import SYMBOL_RE, _safe_csv
from nse_backtest.data import fetch_multiple, fetch_nifty50, NIFTY50_SYMBOLS, NIFTY100_SYMBOLS
from nse_backtest.trading_modes import analyze_swing, analyze_positional, analyze_longterm, analyze_intraday
from nse_backtest.sample_data import trending_stock, volatile_midcap, sideways_stock
_ANALYZE_FNS = {"Swing (2-15d)": analyze_swing, "Positional (15-90d)": analyze_positional,
                "Long-Term (90d+)": analyze_longterm, "Intraday": analyze_intraday}

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

st.markdown("# 📊 Stock Screener")
st.markdown("_Scan the market. Click any row to open full analysis._")

sc1, sc2, sc3, sc4 = st.columns([2, 2, 1, 2])
mode = sc1.selectbox("Trading Mode", list(_ANALYZE_FNS.keys()), key="scr_mode")
universe = sc2.selectbox("Universe", ["Nifty 50 (Live)", "Nifty 100 (Live)", "Custom", "Demo"], key="scr_univ")
max_n = sc3.number_input("Max results", 1, 50, 15, key="scr_max")
custom_input = sc4.text_input("Custom symbols (comma-sep)", key="scr_custom", disabled=(universe != "Custom"))

last_scan = st.session_state.get("scr_last_scan")
if last_scan:
    st.caption(f"Last scan: {last_scan}")

if st.button("🔍 Scan", type="primary", use_container_width=True, key="scr_run"):
    if universe == "Custom":
        syms = [s.strip().upper() for s in custom_input.split(",") if s.strip() and SYMBOL_RE.match(s.strip().upper())]
        raw_data = fetch_multiple(syms, start="2021-01-01")
    elif universe == "Demo":
        raw_data = {"DEMO_UP": trending_stock(), "DEMO_MID": volatile_midcap(),
                    "DEMO_FLAT": sideways_stock(), "DEMO_UP2": trending_stock()}
    elif "100" in universe:
        raw_data = fetch_multiple(NIFTY100_SYMBOLS, start="2021-01-01")
    else:
        raw_data = fetch_multiple(NIFTY50_SYMBOLS, start="2021-01-01")

    analyze_fn = _ANALYZE_FNS[mode]
    cap, risk = state.get_capital(), state.get_risk_pct()
    # Skip Nifty fetch entirely on Demo (synthetic universe doesn't need it).
    nifty_for_engine = None if universe == "Demo" else _cached_nifty()
    rows = []
    prog = st.progress(0, text="Scanning…")
    total = len(raw_data)
    for i, (sym, df) in enumerate(raw_data.items()):
        prog.progress((i + 1) / total, text=f"Scanning {sym}…")
        if len(df) < 60:
            continue
        try:
            setup = analyze_fn(df, sym, cap, risk, nifty_df=nifty_for_engine)
            if setup:
                rows.append({"Symbol": sym, "Signal": setup.signal, "Score": round(setup.score, 0),
                              "Setup": getattr(setup, "strategy_name", mode) or mode,
                              "Entry ₹": round(setup.entry_price, 0), "SL ₹": round(setup.stop_loss, 0),
                              "Target ₹": round(setup.target_1, 0), "R:R": round(setup.risk_reward, 2),
                              "Win %": round(setup.win_probability, 0)})
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
                            theme="alpine-dark",
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
