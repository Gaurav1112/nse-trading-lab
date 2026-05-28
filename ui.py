import streamlit as st
from components import theme, state

st.set_page_config(page_title="NSE Trading Lab", page_icon="◆", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

with st.sidebar:
    st.markdown('<div style="padding:8px 0 16px;font-size:20px;font-weight:700;'
                'letter-spacing:-0.02em">◆ Trading Lab</div>', unsafe_allow_html=True)
    st.markdown("---")
    st.markdown('<span style="font-size:11px;color:#64748b;text-transform:uppercase;'
                'letter-spacing:.1em">Quick Watchlist</span>', unsafe_allow_html=True)
    for sym in state.get_watchlist()[:5]:
        if st.button(f"  {sym}", key=f"wl_{sym}", use_container_width=True):
            state.set_analyze_sym(sym)
            st.session_state["auto_analyze"] = True
            st.switch_page("pages/3_Analyze.py")
    st.markdown("---")
    cap = state.get_capital()
    st.caption(f"💰 ₹{cap:,.0f}")
    st.caption(f"🎯 {state.get_risk_pct()}% risk = ₹{cap * state.get_risk_pct() / 100:,.0f}/trade")

st.markdown("# ◆ NSE Trading Lab")
st.markdown("##### Your personal market intelligence terminal for real-world NSE trading")
st.markdown("---")
st.info("👈 Use the sidebar to navigate. Start with **Today's Picks** to find actionable trades.", icon="◆")
