import streamlit as st
from components import theme, state

st.set_page_config(page_title="NSE Trading Lab", page_icon="📈", layout="wide",
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

# If the user already has open positions, route them straight to Decay Watch
# on first load so monitoring takes priority over discovery. Industry analog:
# Robinhood opens to portfolio, not discover. Toggle off via session state
# `seen_home` so subsequent navigation back to home behaves normally.
_open_positions = [p for p in state.get_positions() if not p.get("closed_date")]
if _open_positions and not st.session_state.get("seen_home"):
    st.session_state["seen_home"] = True
    st.switch_page("pages/12_Decay_Watch.py")

st.markdown("# ◆ NSE Trading Lab")
st.markdown("##### Your personal market intelligence terminal for real-world NSE trading")
st.markdown("---")
if _open_positions:
    st.warning(
        f"💼 You have **{len(_open_positions)} open position(s)** — monitor them on **Decay Watch** "
        f"(page 12) before considering new entries."
    )
else:
    st.info("👈 Use the sidebar to navigate. Start with **Today's Picks** to find actionable trades.")

# ── SEBI disclaimer footer (compliance: not investment advice) ──────────────
st.markdown("---")
st.caption(
    "**Disclaimer.** This tool is personal research software. It is **not** investment advice. "
    "The author is **not** a SEBI-registered investment adviser. Past performance, walk-forward "
    "expectancy estimates, and engine verdicts do not guarantee future results. Held-out 2026 YTD "
    "data showed -1.61% expectancy per trade on the v2 engine in HOSTILE tape. "
    "Trade only with capital you can afford to lose. Consult a SEBI-registered adviser before "
    "deploying significant capital based on outputs from this software."
)
