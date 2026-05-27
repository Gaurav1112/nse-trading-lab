import re
import streamlit as st
from components import theme, state, cards
from nse_backtest.data import fetch_nse
from nse_backtest.trading_modes import analyze_all_modes
from nse_backtest.sample_data import trending_stock

SYMBOL_RE = re.compile(r"^[A-Z0-9&.\-^]{1,20}$")

st.set_page_config(page_title="Trading Modes | Trading Lab", page_icon="📈", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# 📈 Multi-Mode Analysis")
st.markdown("_One stock, 5 trading perspectives — swing to options._")


@st.cache_data(ttl=300, show_spinner=False)
def _fetch(sym: str):
    try:
        df = fetch_nse(sym, start="2020-01-01")
        if df is None or len(df) < 50:
            return trending_stock(), True
        return df, False
    except Exception:
        return trending_stock(), True


tm1, tm2 = st.columns([3, 1])
with tm1:
    tm_sym_raw = st.text_input("Symbol", "COALINDIA", key="tm_sym")
with tm2:
    tm_demo = st.checkbox("Demo", False, key="tm_demo")

if st.button("⚡  Analyze All Modes", type="primary", use_container_width=True) and tm_sym_raw:
    s = tm_sym_raw.upper().strip()
    if not SYMBOL_RE.match(s):
        st.error(f"Invalid symbol '{tm_sym_raw}'. Use letters/numbers only (e.g. RELIANCE, M&M).")
        st.stop()
    tm_sym = s
    with st.spinner(f"Running 5 analysis modes on {tm_sym}..."):
        tm_df = trending_stock() if tm_demo else _fetch(tm_sym)[0]
        results = analyze_all_modes(tm_df, tm_sym, state.get_capital())

    # ── Overview Strip ──
    st.markdown("### At a Glance")
    oc = st.columns(5)
    modes_display = [
        ("🔄 Swing", results.get("swing"), "2-15d"),
        ("📊 Positional", results.get("positional"), "15-90d"),
        ("🏦 Long Term", results.get("longterm"), "90d+"),
        ("⚡ Intraday", results.get("intraday"), "Today"),
        ("📑 Options", None, ""),
    ]
    for i, (label, setup, tf) in enumerate(modes_display):
        if setup and hasattr(setup, 'signal'):
            sig = setup.signal
            sc = setup.score
            clr = "#10b981" if sig == "BUY" else "#ef4444" if sig == "SELL" else "#f59e0b"
            sig_icon = "✅" if sig == "BUY" else "🚫" if sig == "SELL" else "⏳"
            oc[i].markdown(f'<div style="text-align:center;padding:12px;background:#111827;border-radius:10px;border:1px solid {clr}"><span style="font-size:11px;color:#64748b">{label}</span><br><span style="font-size:20px;font-weight:700;color:{clr}">{sig_icon} {sig}</span><br><span style="font-size:12px;color:#94a3b8">{sc:.0f}/100 | {tf}</span></div>', unsafe_allow_html=True)
        elif label == "📑 Options":
            opts = results.get("options")
            if opts:
                oc[i].markdown(f'<div style="text-align:center;padding:12px;background:#111827;border-radius:10px;border:1px solid #8b5cf6"><span style="font-size:11px;color:#64748b">{label}</span><br><span style="font-size:20px;font-weight:700;color:#8b5cf6">{opts.outlook}</span><br><span style="font-size:12px;color:#94a3b8">IV Rank {opts.iv_rank:.0f}%</span></div>', unsafe_allow_html=True)

    # ── Tabs for each mode ──
    tab_s, tab_p, tab_l, tab_i, tab_o, tab_f = st.tabs(["🔄 Swing", "📊 Positional", "🏦 Long Term", "⚡ Intraday", "📑 Options", "📜 Futures"])

    def show_trade_setup(setup):
        if not setup or setup.signal == "ERROR":
            st.error(f"Analysis failed: {setup.reasons[0] if setup.reasons else 'Unknown error'}")
            return
        clr = "#10b981" if setup.signal == "BUY" else "#ef4444" if setup.signal == "SELL" else "#f59e0b"
        st.markdown(f'<div style="background:#111827;padding:16px;border-radius:12px;border-left:4px solid {clr};margin:8px 0"><span style="font-size:22px;font-weight:700;color:{clr}">{setup.signal}</span> <span style="color:#64748b">| Score {setup.score:.0f}/100 | Win Prob {setup.win_probability:.0f}% | {setup.timeframe}</span></div>', unsafe_allow_html=True)
        mc = st.columns(6)
        mc[0].metric("Entry", f"₹{setup.entry_price:,.0f}")
        mc[1].metric("SL", f"₹{setup.stop_loss:,.0f}")
        mc[2].metric("T1", f"₹{setup.target_1:,.0f}")
        mc[3].metric("T2", f"₹{setup.target_2:,.0f}")
        mc[4].metric("R:R", f"{setup.risk_reward:.1f}:1")
        mc[5].metric("Qty", f"{setup.suggested_qty}")
        if setup.suggested_qty > 0:
            st.markdown(f"Position: **{setup.suggested_qty}** shares = ₹{setup.position_value:,.0f} | Max loss: ₹{setup.max_loss:,.0f}")
        for r in setup.reasons:
            st.caption(f"• {r}")
        for w in setup.warnings:
            st.markdown(f'<div class="card-y">⚠️ {w}</div>', unsafe_allow_html=True)

    with tab_s:
        st.markdown("### 🔄 Swing Trading (2-15 days)")
        st.markdown("_Momentum breakouts, mean reversion, technical signals._")
        show_trade_setup(results.get("swing"))

    with tab_p:
        st.markdown("### 📊 Positional Trading (15-90 days)")
        st.markdown("_Trend following with wider stops. Sector strength + weekly momentum._")
        show_trade_setup(results.get("positional"))

    with tab_l:
        st.markdown("### 🏦 Long-Term Investing (90+ days)")
        st.markdown("_CAGR, accumulation patterns, value analysis. 15% trailing stop._")
        show_trade_setup(results.get("longterm"))

    with tab_i:
        st.markdown("### ⚡ Intraday Trading (Same Day)")
        st.markdown("_VWAP, Opening Range Breakout, volume spikes. Use MIS on Zerodha._")
        show_trade_setup(results.get("intraday"))

    with tab_o:
        st.markdown("### 📑 Options Analysis")
        opts = results.get("options")
        if opts:
            oi1, oi2, oi3, oi4 = st.columns(4)
            oi1.metric("Spot", f"₹{opts.spot_price:,.0f}")
            oi2.metric("IV (HV proxy)", f"{opts.iv_current:.1f}%")
            oi3.metric("IV Rank", f"{opts.iv_rank:.0f}%")
            oi4.metric("IV Percentile", f"{opts.iv_percentile:.0f}%")
            mp1, mp2, mp3 = st.columns(3)
            mp1.metric("Max Pain (est)", f"₹{opts.max_pain:,.0f}")
            mp2.metric("PCR (est)", f"{opts.pcr:.2f}")
            mp3.metric("Outlook", opts.outlook)
            if opts.iv_rank > 50:
                st.markdown(f'<div class="card-y">📊 IV Rank {opts.iv_rank:.0f}% — above average. Favor <b>selling premium</b> (credit spreads, iron condors)</div>', unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="card">📊 IV Rank {opts.iv_rank:.0f}% — below average. Options are <b>cheap</b> — favor buying (long calls/puts, straddles)</div>', unsafe_allow_html=True)
            st.markdown("### Strategy Suggestions")
            for strat in opts.strategies:
                with st.expander(f"{'🟢' if 'Bull' in strat['name'] or 'Long Call' in strat['name'] else '🔴' if 'Bear' in strat['name'] or 'Long Put' in strat['name'] else '🟡'} {strat['name']}"):
                    st.markdown(f"**Legs:** {strat['legs']}")
                    st.markdown(f"**Thesis:** {strat['thesis']}")
                    st.markdown(f"**Max Profit:** {strat['max_profit']}")
                    st.markdown(f"**Max Loss:** {strat['max_loss']}")
                    st.markdown(f"**Breakeven:** {strat['breakeven']}")
            st.markdown('---')
            st.caption("⚠️ Options data is estimated from price/volatility. For real OI/IV, use Zerodha Sensibull or NSE Option Chain.")

    with tab_f:
        st.markdown("### 📜 Futures Analysis")
        fut = results.get("futures")
        if fut:
            fc = st.columns(4)
            fc[0].metric("Spot", f"₹{fut.spot_price:,.0f}")
            fc[1].metric("Futures (est)", f"₹{fut.futures_price:,.0f}")
            fc[2].metric("Basis", f"₹{fut.basis:,.0f} ({fut.basis_pct:.1f}%)")
            sig_color = "#10b981" if "LONG" in fut.signal else "#ef4444" if "SHORT" in fut.signal else "#f59e0b"
            fc[3].markdown(f'<div style="text-align:center;padding:12px;background:#111827;border-radius:8px;border:1px solid {sig_color}"><span style="font-size:11px;color:#64748b">OI Signal</span><br><span style="font-size:16px;font-weight:700;color:{sig_color}">{fut.signal}</span></div>', unsafe_allow_html=True)
            for r in fut.reasons:
                st.markdown(f"• {r}")
            st.markdown("""
### Futures Signal Guide
| Price | Volume/OI | Signal | Meaning |
|---|---|---|---|
| ↑ | ↑ | LONG BUILD | Fresh buying — bullish |
| ↑ | ↓ | SHORT COVER | Shorts closing — weakly bullish |
| ↓ | ↑ | SHORT BUILD | Fresh selling — bearish |
| ↓ | ↓ | LONG UNWIND | Longs closing — weakly bearish |
            """)
            st.caption("⚠️ Futures data is estimated. For real OI/rollover data, use Zerodha Sensibull or NSE website.")
