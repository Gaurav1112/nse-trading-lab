"""My Track Record — your own P&L curve, win rate, and rolling Sharpe.

Until your own ledger crosses ~50 closed trades, trust this page over
the walk-forward verdicts. Walk-forward is what the engine could do
historically; this is what you've actually done.
"""
import pandas as pd
import streamlit as st

from components import state, theme
from components.pnl_tracker import snapshot

st.set_page_config(page_title="Track Record | Trading Lab", page_icon="📈", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# 📈 My Track Record")
st.caption(
    "Your own closed-trade record. The walk-forward A/B tells you what the "
    "engine *could* have done historically; this page tells you what you *have* "
    "done. Trust your own data after ~50 trades — until then, this is "
    "preliminary and noise-dominated."
)

snap = snapshot(state.get_journal(), state.get_capital())

m1, m2, m3, m4 = st.columns(4)
m1.metric("Closed trades", snap.n_closed)
m2.metric("Win rate", f"{snap.win_rate_pct:.1f}%")
m3.metric("Expectancy / trade", f"{snap.expectancy_pct:+.2f}%")
m4.metric("Rolling 30d Sharpe", f"{snap.rolling_30d_sharpe:+.2f}")

st.divider()

if snap.cumulative_returns_pct:
    st.markdown("### Cumulative %  (trade-by-trade)")
    df = pd.DataFrame({"cumulative_%": snap.cumulative_returns_pct})
    st.line_chart(df)
else:
    st.info("📊 Your P&L curve will appear here after your first closed trade.")

if snap.notes:
    st.markdown("### Notes")
    for n in snap.notes:
        st.warning(n)

st.divider()
st.caption(
    "Reference: walk-forward A/B v2 expectancy was +0.34% in HOSTILE 2025, "
    "+2.01% in MIXED 2024, +7.17% in TRENDING 2023. Held-out 2026 YTD "
    "showed -1.61% with Wave A active — strong evidence the HOSTILE-tape "
    "edge sits at or below zero."
)
