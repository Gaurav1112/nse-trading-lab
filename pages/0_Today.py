import streamlit as st
from components import theme, state
from components.state_reader import read_latest, read_health
from components.regime_cockpit import render_cockpit

st.set_page_config(page_title="Today | Trading Lab", page_icon="🎯", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# 🎯 Today")

health = read_health()
if health is None:
    st.error("Pipeline state not found — the signals repo isn't cloned locally. Run `scripts/clone_signals.sh` once.")
    st.stop()

latest = read_latest()
if latest is None:
    st.warning("Pipeline hasn't produced its first batch yet.")
    st.stop()

st.caption(f"Pipeline status: **{health.get('status')}** · Last run: `{health.get('last_run_ts')}`")
render_cockpit(latest)
st.markdown("---")
st.markdown(f"Signals in this batch: **{len(latest.get('signals', []))}**")

for s in latest.get("signals", []):
    st.markdown(f"- **{s['symbol']}** · {s['action']} @ ₹{s['entry']:.2f} · SL {s['stop_loss']:.2f} · Tgt {s['target']:.2f}")
