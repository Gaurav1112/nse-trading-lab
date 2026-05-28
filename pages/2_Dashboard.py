import streamlit as st
import yfinance as yf
from components import theme, state, cards, charts, market_data
from components.market_data import get_live_price
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
idx_border = {"Nifty 50": "#4D9FFF", "Nifty Bank": "#A78BFA", "India VIX": "#FFB800"}
for i, (name, data) in enumerate(indices.items()):
    with idx_cols[i]:
        if data["price"] is not None:
            chg = data["change_pct"] or 0.0
            color = "#00FF87" if chg >= 0 else "#FF3355"
            st.markdown(cards.metric_card(name, f"{data['price']:,.2f}", f"{chg:+.2f}%",
                                           idx_border.get(name, "#4D9FFF")), unsafe_allow_html=True)
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
    color = "#00FF87" if pct >= 60 else "#FFB800" if pct >= 40 else "#FF3355"
    sentiment = "🟢 Bullish" if pct >= 60 else "🟡 Neutral" if pct >= 40 else "🔴 Bearish"
    st.markdown(
        f'<div style="background:#0D1526;padding:16px;border-radius:10px;border:1px solid #1E3A5F;text-align:center">'
        f'<div style="font-size:36px;font-weight:700;color:{color};font-family:JetBrains Mono,monospace">{pct:.0f}%</div>'
        f'<div style="font-size:12px;color:#5A7390;margin-top:4px">{breadth["above"]} of {breadth["total"]} stocks above EMA20</div>'
        f'<div style="font-size:11px;color:#7A93AA;margin-top:8px">{sentiment}</div>'
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
                f'<div style="background:#071a10;padding:8px 12px;border-radius:8px;margin:4px 0;'
                f'display:flex;justify-content:space-between">'
                f'<span style="font-weight:600">{m["symbol"]}</span>'
                f'<span style="color:#00FF87;font-family:JetBrains Mono,monospace">{m["change_pct"]:+.2f}%</span>'
                f'</div>', unsafe_allow_html=True)
    with mc2:
        st.caption("🔴 Top Losers")
        for m in movers["losers"]:
            st.markdown(
                f'<div style="background:#1a0709;padding:8px 12px;border-radius:8px;margin:4px 0;'
                f'display:flex;justify-content:space-between">'
                f'<span style="font-weight:600">{m["symbol"]}</span>'
                f'<span style="color:#FF3355;font-family:JetBrains Mono,monospace">{m["change_pct"]:+.2f}%</span>'
                f'</div>', unsafe_allow_html=True)

st.markdown("---")

# ── Sector heatmap ──
st.markdown("#### 🗺️ Sector Performance")
with st.spinner("Loading sectors…"):
    sector_perf = market_data.get_sector_performance()
st.plotly_chart(charts.make_sector_heat(sector_perf), use_container_width=True)

st.markdown("---")

# ── Watchlist — uses fast_info so split-adjusted prices don't corrupt display ──
st.markdown("#### ⭐ My Watchlist")
watchlist = state.get_watchlist()
if watchlist:
    wl_cols = st.columns(min(len(watchlist), 5))
    for i, sym in enumerate(watchlist[:5]):
        with wl_cols[i]:
            try:
                price, prev = get_live_price(sym)
                if price and prev:
                    chg = (price - prev) / prev * 100
                    color = "#00FF87" if chg >= 0 else "#FF3355"
                    st.markdown(cards.metric_card(sym, f"₹{price:,.0f}", f"{chg:+.2f}%", color), unsafe_allow_html=True)
                else:
                    st.metric(sym, "N/A")
            except Exception:
                st.metric(sym, "N/A")
if st.button("⚙️ Edit Watchlist"):
    st.switch_page("pages/11_Settings.py")
