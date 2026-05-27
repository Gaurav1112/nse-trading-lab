import streamlit as st
from components import theme, state
from nse_backtest.strategies import STRATEGIES
from nse_backtest.data import NIFTY100_SYMBOLS

st.set_page_config(page_title="Settings | Trading Lab", page_icon="⚙️", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()
st.markdown("# ⚙️ Settings")

cap = st.number_input("Capital (₹)", min_value=1_000.0, max_value=1e8,
                       value=state.get_capital(), step=10_000.0, format="%.0f")
risk = st.slider("Risk per trade (%)", 0.5, 5.0, state.get_risk_pct(), 0.5)
sl = st.slider("Default Stop Loss (%)", 1.0, 15.0, state.get_sl_pct(), 0.5)
if st.button("💾 Save Settings", type="primary"):
    state.set_capital(cap); state.set_risk_pct(risk); state.set_sl_pct(sl)
    st.success(f"✅ Saved — max loss/trade: ₹{cap * risk / 100:,.0f}")

st.markdown("---")
st.markdown("**Watchlist**")
wl_str = st.text_input("Symbols (comma-separated)", value=", ".join(state.get_watchlist()))
if st.button("Update Watchlist"):
    new_wl = [s.strip().upper() for s in wl_str.split(",") if s.strip()]
    state.set_watchlist(new_wl)
    st.success(f"Updated: {new_wl}")
st.markdown("---")
st.caption(f"Strategies: {len(STRATEGIES)} | Nifty 100 universe: {len(NIFTY100_SYMBOLS)} stocks")
