import streamlit as st
import pandas as pd
from components import theme, state
from nse_backtest.risk import (kelly_criterion, fractional_kelly, volatility_target_size,
                                position_size_risk_based, calmar_ratio, compute_var_cvar,
                                monthly_returns_table)
from nse_backtest.engine import run_backtest, TradeConfig
from nse_backtest.strategies import STRATEGIES
from nse_backtest.analytics import compute_metrics
from nse_backtest.sample_data import trending_stock

st.set_page_config(page_title="Risk Lab | Trading Lab", page_icon="🛡️", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()
st.markdown("# 🛡️ Risk Lab")

tab1, tab2, tab3 = st.tabs(["📐 Position Sizing", "🎰 Kelly Calculator", "📊 Portfolio Risk"])

with tab1:
    st.markdown("### How many shares to buy?")
    rc = st.columns(3)
    with rc[0]: rp = st.number_input("Price ₹", value=470.0, min_value=1.0, format="%.2f"); rsl = st.number_input("SL ₹", value=440.0, min_value=0.1, format="%.2f")
    with rc[1]: rcap = st.number_input("Capital ₹", value=int(state.get_capital()), min_value=1000, step=10000); rr = st.slider("Risk %", 0.5, 5.0, state.get_risk_pct(), 0.5, key="rsk")
    with rc[2]: ratr = st.number_input("ATR ₹", value=15.0, min_value=0.1, format="%.2f"); rwr = st.slider("Win Rate %", 20, 80, 55, key="wr")
    if st.button("Calculate", type="primary", use_container_width=True, key="calc_pos"):
        ff = position_size_risk_based(rcap, rp, rsl, rr / 100)
        vt = volatility_target_size(rcap, rp, ratr, 0.15)
        kf = fractional_kelly(rwr / 100, 2 * (rp - rsl), rp - rsl, 0.25)
        ks = int(rcap * kf / rp) if kf > 0 else 0
        mc = st.columns(3)
        with mc[0]: st.markdown("**Fixed Fractional**"); st.metric("Shares", ff); st.metric("Position", f"₹{ff * rp:,.0f}")
        with mc[1]: st.markdown("**Vol Target**"); st.metric("Shares", vt); st.metric("Position", f"₹{vt * rp:,.0f}")
        with mc[2]: st.markdown("**Kelly**"); st.metric("Shares", ks); st.metric("Position", f"₹{ks * rp:,.0f}")

with tab2:
    st.markdown("### Kelly Criterion — Optimal Bet Size")
    kwr = st.slider("Win Rate %", 30, 75, 55, key="k2"); kaw = st.number_input("Avg Win ₹", value=5000.0, min_value=1.0, format="%.0f")
    kal = st.number_input("Avg Loss ₹", value=3000.0, min_value=1.0, format="%.0f")
    fk = kelly_criterion(kwr / 100, kaw, kal) * 100; qk = fractional_kelly(kwr / 100, kaw, kal, 0.25) * 100
    st.metric("Full Kelly", f"{fk:.1f}%"); st.metric("Quarter Kelly ★", f"{qk:.1f}%")
    if fk <= 0: st.error("Kelly says DON'T trade — losing system")
    else: st.success(f"Risk **{qk:.1f}%** per trade = ₹{state.get_capital() * qk / 100:,.0f}")

with tab3:
    st.markdown("### Portfolio Risk Metrics")
    demo_df = trending_stock(); cfg = TradeConfig(initial_capital=state.get_capital(), stop_loss_pct=state.get_sl_pct() / 100)
    r = run_backtest(STRATEGIES["ema_filtered"](demo_df), cfg)
    eq = r["equity_curve"]; m = compute_metrics(r); var = compute_var_cvar(eq)
    cal = calmar_ratio(m["cagr_pct"], abs(m["max_drawdown_pct"]))
    rc = st.columns(4)
    rc[0].metric("Sharpe", f"{m['sharpe_ratio']:.2f}"); rc[1].metric("Calmar", f"{cal:.2f}")
    rc[2].metric("VaR 95%", f"{var['var_95'] * 100:.2f}%"); rc[3].metric("Max DD", f"{m['max_drawdown_pct']:.1f}%")
    mt = monthly_returns_table(eq)
    if len(mt) > 0:
        st.markdown("### Monthly Returns (%)")
        st.dataframe(mt.style.format("{:.1f}").background_gradient(cmap="RdYlGn", vmin=-10, vmax=10), use_container_width=True)
