import streamlit as st
import yfinance as yf
from components import theme, state, cards, charts, market_data
from nse_backtest.data import NIFTY100_SYMBOLS

st.set_page_config(page_title="Dashboard | Trading Lab", page_icon="📊", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# 📊 Market Dashboard")
st.caption("NSE data delayed ~15 min via yfinance")
if st.button("🔄 Refresh", key="dash_refresh"):
    st.cache_data.clear(); st.rerun()

# ── Index strip ──
with st.spinner("Loading indices…"):
    indices = market_data.get_indices()
idx_cols = st.columns(3)
idx_border = {"Nifty 50": "#3b82f6", "Nifty Bank": "#8b5cf6", "India VIX": "#f59e0b"}
for i, (name, data) in enumerate(indices.items()):
    with idx_cols[i]:
        if data["price"] is not None:
            chg = data["change_pct"] or 0.0
            color = "#10b981" if chg >= 0 else "#ef4444"
            st.markdown(cards.metric_card(name, f"{data['price']:,.2f}", f"{chg:+.2f}%",
                                           idx_border.get(name, "#3b82f6")), unsafe_allow_html=True)
        else:
            st.warning(f"{name}: unavailable")

st.markdown("---")

# ── Breadth + Movers ──
col_b, col_m = st.columns([1, 2])
with col_b:
    st.markdown("#### 📊 Market Breadth")
    with st.spinner("Computing…"):
        breadth = market_data.get_market_breadth(tuple(NIFTY100_SYMBOLS))
    pct = breadth["pct"]
    color = "#10b981" if pct >= 60 else "#f59e0b" if pct >= 40 else "#ef4444"
    sentiment = "🟢 Bullish" if pct >= 60 else "🟡 Neutral" if pct >= 40 else "🔴 Bearish"
    st.markdown(
        f'<div style="background:#111827;padding:16px;border-radius:10px;border:1px solid #1e2a42;text-align:center">'
        f'<div style="font-size:36px;font-weight:700;color:{color};font-family:JetBrains Mono,monospace">{pct:.0f}%</div>'
        f'<div style="font-size:12px;color:#64748b;margin-top:4px">{breadth["above"]} of {breadth["total"]} stocks above EMA20</div>'
        f'<div style="font-size:11px;color:#94a3b8;margin-top:8px">{sentiment}</div>'
        f'</div>', unsafe_allow_html=True)

with col_m:
    st.markdown("#### 🔥 Top Movers")
    with st.spinner("Loading…"):
        movers = market_data.get_top_movers(tuple(NIFTY100_SYMBOLS))
    mc1, mc2 = st.columns(2)
    with mc1:
        st.caption("🟢 Top Gainers")
        for m in movers["gainers"]:
            st.markdown(
                f'<div style="background:#052e16;padding:8px 12px;border-radius:8px;margin:4px 0;'
                f'display:flex;justify-content:space-between">'
                f'<span style="font-weight:600">{m["symbol"]}</span>'
                f'<span style="color:#10b981;font-family:JetBrains Mono,monospace">{m["change_pct"]:+.2f}%</span>'
                f'</div>', unsafe_allow_html=True)
    with mc2:
        st.caption("🔴 Top Losers")
        for m in movers["losers"]:
            st.markdown(
                f'<div style="background:#2a0a0a;padding:8px 12px;border-radius:8px;margin:4px 0;'
                f'display:flex;justify-content:space-between">'
                f'<span style="font-weight:600">{m["symbol"]}</span>'
                f'<span style="color:#ef4444;font-family:JetBrains Mono,monospace">{m["change_pct"]:+.2f}%</span>'
                f'</div>', unsafe_allow_html=True)

st.markdown("---")

# ── Sector heatmap ──
st.markdown("#### 🗺️ Sector Performance")
with st.spinner("Loading sectors…"):
    sector_perf = market_data.get_sector_performance()
st.plotly_chart(charts.make_sector_heat(sector_perf), use_container_width=True)

st.markdown("---")

# ── Watchlist ──
st.markdown("#### ⭐ My Watchlist")
watchlist = state.get_watchlist()
if watchlist:
    wl_cols = st.columns(min(len(watchlist), 5))
    for i, sym in enumerate(watchlist[:5]):
        with wl_cols[i]:
            try:
                hist = yf.Ticker(f"{sym}.NS").history(period="2d")
                if len(hist) >= 2:
                    price = hist["Close"].iloc[-1]
                    chg = (hist["Close"].iloc[-1] - hist["Close"].iloc[-2]) / hist["Close"].iloc[-2] * 100
                    color = "#10b981" if chg >= 0 else "#ef4444"
                    st.markdown(cards.metric_card(sym, f"₹{price:,.0f}", f"{chg:+.2f}%", color), unsafe_allow_html=True)
                else:
                    st.metric(sym, "N/A")
            except Exception:
                st.metric(sym, "N/A")
if st.button("⚙️ Edit Watchlist"):
    st.switch_page("pages/11_Settings.py")
