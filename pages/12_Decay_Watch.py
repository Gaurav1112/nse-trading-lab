"""Decay Watch — the bagholder antidote page.

Shows every held position with today's re-score, sorted worst-first.
HOLD / TIGHTEN_STOP / EXIT badges. One-glance "what should I exit today?".
"""
import streamlit as st
import pandas as pd
from components import theme, state
from nse_backtest.data import fetch_nse
from nse_backtest.position_monitor import daily_check, ReScoreAction

st.set_page_config(page_title="Decay Watch | Trading Lab", page_icon="⚠️", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# ⚠️ Decay Watch")
st.markdown("_Re-score every held position against today's market. Worst first._")

positions = state.get_positions()
if not positions:
    st.info("No positions yet. Open the **Picks** page and save trades to populate this watch.")
    st.stop()

verdicts = []
with st.spinner(f"Re-scoring {len(positions)} positions…"):
    for pos in positions:
        sym = pos.get("symbol", "")
        if not sym:
            continue
        try:
            df = fetch_nse(sym, start="2022-01-01")
        except Exception as e:
            st.warning(f"{sym}: data fetch failed ({e})")
            continue
        if df is None or len(df) < 60:
            continue
        try:
            v = daily_check(pos, df)
            verdicts.append((v, pos))
        except Exception as e:
            st.warning(f"{sym}: re-score failed ({e})")

_action_order = {ReScoreAction.EXIT: 0, ReScoreAction.TIGHTEN_STOP: 1, ReScoreAction.HOLD: 2}
verdicts.sort(key=lambda vp: (_action_order.get(vp[0].action, 9), vp[0].current_rescore))

if not verdicts:
    st.info("No re-scores produced. Check data availability for your positions.")
    st.stop()

color_map = {
    ReScoreAction.EXIT: "#FF4D4D",
    ReScoreAction.TIGHTEN_STOP: "#FFB800",
    ReScoreAction.HOLD: "#00FF87",
}

for v, pos in verdicts:
    clr = color_map.get(v.action, "#7A93AA")
    st.markdown(
        f'<div style="border:1px solid {clr};border-radius:14px;padding:18px 20px;'
        f'margin:10px 0;background:#0D1526">'
        f'<span style="font-size:22px;font-weight:700;color:{clr}">{v.symbol}</span>'
        f'<span style="font-size:14px;color:#7A93AA;margin-left:12px">'
        f'{v.action} · re-score {v.current_rescore:.0f}/100 · held {v.bars_held} bars</span>'
        f'</div>', unsafe_allow_html=True)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Entry", f"₹{v.entry_price:,.2f}")
    m2.metric("Current", f"₹{v.current_price:,.2f}", delta=f"{v.pnl_pct:+.1f}%",
              delta_color="normal" if v.pnl_pct >= 0 else "inverse")
    if v.suggested_sl is not None:
        m3.metric("Suggested SL", f"₹{v.suggested_sl:,.2f}")
    else:
        m3.metric("Suggested SL", "—")
    m4.metric("Verdict", v.action)
    st.caption(f"💡 {v.reason}")
    st.markdown("---")
