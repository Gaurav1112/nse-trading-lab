import streamlit as st
from components import theme, state, cards, charts
from components.data_freshness import check_freshness
from components.security import SYMBOL_RE
from nse_backtest.data import fetch_nse, fetch_nifty50
from nse_backtest.scorer import analyze_stock
from nse_backtest.sample_data import trending_stock
from nse_backtest.risk import kelly_criterion, position_size_risk_based

st.set_page_config(page_title="Analyze | Trading Lab", page_icon="🔍", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()


# Threaded into analyze_stock so the v2 engine's rs_vs_nifty + regime_gate
# actually fire. Without this, the scorer silently falls back to v1.
@st.cache_data(ttl=3600, show_spinner=False)
def _cached_nifty():
    try:
        return fetch_nifty50(start="2022-01-01")
    except Exception:
        return None


def _validate(sym: str) -> str:
    s = (sym or "").upper().strip()
    if not SYMBOL_RE.match(s):
        raise ValueError(f"Invalid symbol: {sym!r}")
    return s


@st.cache_data(ttl=300, show_spinner=False)
def _fetch(sym: str) -> tuple:
    try:
        df = fetch_nse(_validate(sym), start="2020-01-01")
        if df is None or len(df) < 50:
            return trending_stock(), True
        return df, False
    except ValueError as e:
        st.error(str(e)); return trending_stock(), True
    except Exception as e:
        st.warning(f"Live data unavailable ({e.__class__.__name__}); using demo."); return trending_stock(), True


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_live_price(sym: str) -> float | None:
    """Fetch actual live market price via fast_info — not affected by download adjustment bugs."""
    try:
        import yfinance as yf
        fi = yf.Ticker(f"{sym}.NS").fast_info
        p = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
        return float(p) if p and float(p) > 0 else None
    except Exception:
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def _fetch_fundamentals(sym: str) -> dict:
    sym = _validate(sym)
    import yfinance as yf
    result: dict = {
        "pe": None, "forward_pe": None, "market_cap": None, "eps": None,
        "earnings_growth": None, "revenue_growth": None,
        "debt_to_equity": None, "roe": None, "promoter_pct": None,
    }
    try:
        info = yf.Ticker(f"{sym}.NS").info
        result.update({
            "pe": info.get("trailingPE"), "forward_pe": info.get("forwardPE"),
            "market_cap": info.get("marketCap"), "eps": info.get("trailingEps"),
            "earnings_growth": info.get("earningsGrowth"), "revenue_growth": info.get("revenueGrowth"),
            "debt_to_equity": info.get("debtToEquity"), "roe": info.get("returnOnEquity"),
        })
    except Exception:
        pass
    result["promoter_pct"] = None
    try:
        import requests
        from bs4 import BeautifulSoup
        resp = requests.get(f"https://www.screener.in/company/{sym}/",
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            sh = soup.find(id="shareholding")
            if sh:
                for row in sh.find_all("tr"):
                    cells = row.find_all("td")
                    if cells and "promoter" in cells[0].get_text(strip=True).lower():
                        nums = [td.get_text(strip=True).rstrip("%") for td in cells[1:] if td.get_text(strip=True)]
                        if nums:
                            try: result["promoter_pct"] = float(nums[-1])
                            except ValueError: pass
                        break
    except Exception:
        pass
    return result


st.markdown("# 🔍 Stock Analysis — Pre-Trade Dossier")

col_in, col_btn, col_demo, col_wl = st.columns([3, 1, 1, 1])
sym_input = col_in.text_input("Symbol", value=state.get_analyze_sym(), key="analyze_sym_input").upper().strip()
col_btn.markdown("<br>", unsafe_allow_html=True)
run = col_btn.button("Analyze ▶", type="primary", use_container_width=True)
use_demo = col_demo.checkbox("Demo", key="analyze_demo")
col_wl.markdown("<br>", unsafe_allow_html=True)
if sym_input and col_wl.button("+ Watchlist", use_container_width=True):
    wl = state.get_watchlist()
    if sym_input not in wl:
        wl.append(sym_input); state.set_watchlist(wl)
        st.success(f"Added {sym_input}")

if st.session_state.get("auto_analyze"):
    st.session_state["auto_analyze"] = False; run = True

if run and sym_input:
    state.set_analyze_sym(sym_input)
    with st.spinner(f"Analyzing {sym_input}…"):
        df, is_demo = (trending_stock(), True) if use_demo else _fetch(sym_input)
        if is_demo:
            st.error("⚠️ Could not fetch live data for this symbol. Showing demo data — do NOT trade based on this.")

        # ── Data freshness indicator (Rohan's integrity guard) ────────
        _freshness = check_freshness(df)
        st.markdown(
            f'<div style="border:1px solid {_freshness.color};border-radius:10px;padding:8px 14px;'
            f'margin:6px 0 12px 0;background:#0D1526;font-size:12px;color:#C9D5E0">'
            f'<span style="color:{_freshness.color};font-weight:700">●</span> '
            f'<b>{sym_input}</b> data as of <b>{_freshness.last_bar_date_str}</b> · '
            f'{_freshness.message}</div>',
            unsafe_allow_html=True,
        )
        if _freshness.status == "STALE" and not is_demo:
            st.warning("⚠️ Stale data — analysis below may be misleading. Refresh or verify feed.")
        # ── end freshness ─────────────────────────────────────────────

        score = analyze_stock(df, sym_input, run_backtests=True,
                              nifty_df=None if use_demo else _cached_nifty())

        # ── Price correction + display dataframe ──
        # yfinance auto_adjust=True adjusts all historical bars backward for
        # splits/bonuses so the final bar may not match the actual market price
        # (e.g. LICI: adjusted=415, actual=830 after a 1:1 bonus).
        # We keep the adjusted df for signal generation (EMAs, RSI, etc.) but
        # build a separate disp_df scaled to real market prices for the chart
        # and all header metrics.
        _df_close = float(df["Close"].iloc[-1])
        _live = _fetch_live_price(sym_input)
        _ratio = (_live / _df_close) if (_live and _df_close > 0 and abs(_live / _df_close - 1.0) > 0.05) else 1.0

        # Scale score price levels to match live market prices.
        if _ratio != 1.0:
            score.current_price = _live
            score.stop_loss *= _ratio
            score.target_1 *= _ratio
            score.target_2 *= _ratio

        # disp_df has market-accurate OHLC for chart + 52W metrics.
        if _ratio != 1.0:
            disp_df = df.copy()
            for col in ("Open", "High", "Low", "Close"):
                if col in disp_df.columns:
                    disp_df[col] = disp_df[col] * _ratio
        else:
            disp_df = df

        close = _live if _live else _df_close
        _df_prev = float(df["Close"].iloc[-2]) if len(df) > 1 else _df_close
        prev = _df_prev * _ratio
        day_chg = (close - prev) / prev * 100
        h52 = disp_df["High"].rolling(252).max().iloc[-1]
        l52 = disp_df["Low"].rolling(252).min().iloc[-1]
        vol_ratio = df["Volume"].iloc[-1] / df["Volume"].rolling(20).mean().iloc[-1] if df["Volume"].rolling(20).mean().iloc[-1] > 0 else 1.0
        range_pct = (close - l52) / (h52 - l52) * 100 if h52 != l52 else 50.0
        h1, h2, h3, h4, h5 = st.columns(5)
        _price_label = "Live Price ⚡" if _live else "Last Close"
        h1.metric(_price_label, f"₹{close:,.0f}", f"{day_chg:+.2f}%")
        h2.metric("52W High", f"₹{h52:,.0f}")
        h3.metric("52W Low", f"₹{l52:,.0f}")
        h4.metric("In 52W Range", f"{range_pct:.0f}%")
        h5.metric("Vol vs 20d Avg", f"{vol_ratio:.1f}x")
        st.markdown("---")

        # ── Chart + Verdict ──
        ch_col, v_col = st.columns([2, 1])
        with ch_col:
            period = st.select_slider("Chart period", [60, 120, 250, 500, 1000], 250, key="analyze_period")
            st.plotly_chart(charts.make_candlestick(disp_df, score=score, title=sym_input, period=period),
                            use_container_width=True)
        with v_col:
            st.markdown(cards.verdict_card(score.verdict, score.final_score, score.reasons), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            rr_c = "#00FF87" if score.risk_reward >= 2 else "#FFB800" if score.risk_reward >= 1.5 else "#FF3355"
            st.markdown(cards.metric_card("R:R", f"{score.risk_reward:.1f} : 1", color=rr_c), unsafe_allow_html=True)
            st.markdown(cards.metric_card("Win Probability", f"{score.win_probability:.0f}%"), unsafe_allow_html=True)
            st.markdown(cards.metric_card("Regime", score.regime), unsafe_allow_html=True)
            for w in score.warnings[:3]:
                st.markdown(cards.warning_card(w), unsafe_allow_html=True)
        st.markdown("---")

        # ── 4 Tabs ──
        tab_tech, tab_plan, tab_fund, tab_risk = st.tabs(["📊 Technical", "📋 Trade Plan", "🏦 Fundamentals", "⚖️ Risk"])

        with tab_tech:
            tc1, tc2 = st.columns(2)
            with tc1:
                st.markdown(cards.score_bar("Trend (25%)", score.trend_score, "EMA stack, ADX, higher highs/lows"), unsafe_allow_html=True)
                st.markdown(cards.score_bar("Momentum (20%)", score.momentum_score, "RSI, MACD, StochRSI"), unsafe_allow_html=True)
                st.markdown(cards.score_bar("Volume (15%)", score.volume_score, "OBV trend, vol spikes"), unsafe_allow_html=True)
            with tc2:
                st.markdown(cards.score_bar("Volatility (10%)", score.volatility_score, "ATR range, BB squeeze"), unsafe_allow_html=True)
                st.markdown(cards.score_bar("Backtest (15%)", score.backtest_score, "Historical strategy profitability"), unsafe_allow_html=True)
                st.markdown(cards.score_bar("Risk (15%)", score.risk_score, "Support proximity, distance from highs"), unsafe_allow_html=True)
            kc1, kc2, kc3 = st.columns(3)
            kc1.metric("Ichimoku", score.ichimoku_signal)
            kc2.metric("SAR", score.sar_signal)
            kc3.metric("Confidence", score.confidence)
            with st.expander("All Technical Signals"):
                for r in score.reasons: st.caption(f"• {r}")

        with tab_plan:
            cap = state.get_capital(); risk_pct = state.get_risk_pct()
            shares = position_size_risk_based(cap, score.current_price, score.stop_loss, risk_pct)
            pos_val = shares * score.current_price
            max_loss = (score.current_price - score.stop_loss) * shares
            pc1, pc2, pc3 = st.columns(3)
            pc1.metric("Entry Zone", score.entry_zone)
            pc2.metric("Stop Loss", f"₹{score.stop_loss:,.0f}", f"-{(score.current_price-score.stop_loss)/score.current_price*100:.1f}%" if score.current_price > 0 else "")
            pc3.metric("Target 1", f"₹{score.target_1:,.0f}", f"+{(score.target_1-score.current_price)/score.current_price*100:.1f}%" if score.current_price > 0 else "")
            pc4, pc5, pc6 = st.columns(3)
            pc4.metric("Target 2", f"₹{score.target_2:,.0f}", f"+{(score.target_2-score.current_price)/score.current_price*100:.1f}%" if score.current_price > 0 else "")
            pc5.metric("Suggested Qty", f"{shares} shares", f"₹{pos_val:,.0f}")
            pc6.metric("Max Loss", f"₹{max_loss:,.0f}", f"{risk_pct:.1f}% capital")
            st.markdown(cards.zerodha_steps(sym_input, score.current_price, score.stop_loss, shares), unsafe_allow_html=True)
            st.caption("⚠️ Prices based on previous day's close. Verify live price on Zerodha before trading.")

        with tab_fund:
            st.caption("Source: yfinance (P/E, ROE, EPS) + screener.in (promoter %) — 24hr cache")
            with st.spinner("Loading fundamentals…"):
                fund = _fetch_fundamentals(sym_input)
            if not any(v is not None for v in fund.values()):
                st.warning("Fundamentals unavailable for this symbol.")
            else:
                fc1, fc2, fc3 = st.columns(3)
                fc1.metric("Trailing P/E", f"{fund.get('pe'):.1f}" if fund.get('pe') else "N/A")
                fc2.metric("Forward P/E", f"{fund.get('forward_pe'):.1f}" if fund.get('forward_pe') else "N/A")
                fc3.metric("Trailing EPS", f"₹{fund.get('eps'):.2f}" if fund.get('eps') else "N/A")
                fc4, fc5, fc6 = st.columns(3)
                fc4.metric("Revenue Growth", f"{fund.get('revenue_growth')*100:.1f}%" if fund.get('revenue_growth') else "N/A")
                fc5.metric("Earnings Growth", f"{fund.get('earnings_growth')*100:.1f}%" if fund.get('earnings_growth') else "N/A")
                fc6.metric("ROE", f"{fund.get('roe')*100:.1f}%" if fund.get('roe') else "N/A")
                fc7, fc8, fc9 = st.columns(3)
                fc7.metric("Debt / Equity", f"{fund.get('debt_to_equity'):.2f}" if fund.get('debt_to_equity') else "N/A")
                mkt = fund.get('market_cap')
                fc8.metric("Market Cap", f"₹{mkt/1e7:.0f}L Cr" if mkt and mkt > 1e7 else (f"₹{mkt/1e5:.0f}K Cr" if mkt else "N/A"))
                fc9.metric("Promoter Holding", f"{fund.get('promoter_pct'):.1f}%" if fund.get('promoter_pct') else "N/A")
                red_flags = []
                if fund.get('earnings_growth') and fund.get('earnings_growth') < -0.05:
                    red_flags.append(f"EPS declining {fund.get('earnings_growth')*100:.1f}%")
                if fund.get('debt_to_equity') and fund.get('debt_to_equity') > 1.5:
                    red_flags.append(f"High leverage D/E {fund.get('debt_to_equity'):.2f} > 1.5")
                if fund.get('promoter_pct') and fund.get('promoter_pct') < 30:
                    red_flags.append(f"Low promoter holding {fund.get('promoter_pct'):.1f}% < 30%")
                if fund.get('roe') and fund.get('roe') < 0.10:
                    red_flags.append(f"Weak ROE {fund.get('roe')*100:.1f}% < 10%")
                if red_flags:
                    st.markdown("**⚠️ Red Flags:**")
                    for f_flag in red_flags: st.markdown(cards.warning_card(f_flag), unsafe_allow_html=True)
                else:
                    st.success("✅ No fundamental red flags detected")

        with tab_risk:
            cap = state.get_capital(); risk_pct = state.get_risk_pct()
            shares = position_size_risk_based(cap, score.current_price, score.stop_loss, risk_pct)
            rc1, rc2, rc3 = st.columns(3)
            rc1.metric("Win Probability", f"{score.win_probability:.0f}%")
            rc2.metric("Expected Value", f"{score.expected_value_pct:.1f}%")
            rc3.metric("Expected Gain", f"{score.expected_gain_pct:.1f}%")
            try:
                kf = kelly_criterion(score.win_probability/100, score.expected_gain_pct/100, abs(score.expected_loss_pct)/100)
                st.info(f"Kelly fraction: {kf*100:.1f}% — use ¼ Kelly = {kf*25:.1f}% of capital")
            except Exception:
                pass
            st.markdown("**Trade Scenarios:**")
            for label, price, color in [("🔴 SL Hit", score.stop_loss, "#FF3355"),
                                         ("🟡 Target 1", score.target_1, "#FFB800"),
                                         ("🟢 Target 2", score.target_2, "#00FF87")]:
                pnl = (price - score.current_price) * shares
                pct = pnl / cap * 100
                st.markdown(
                    f'<div style="background:#0D1526;padding:12px 16px;border-radius:8px;'
                    f'border-left:3px solid {color};margin:6px 0;display:flex;justify-content:space-between">'
                    f'<span>{label} @ ₹{price:,.0f}</span>'
                    f'<span style="font-family:JetBrains Mono,monospace;color:{color}">'
                    f'{pnl:+,.0f} ({pct:+.2f}% capital)</span></div>',
                    unsafe_allow_html=True)
elif not sym_input:
    st.info("Enter a symbol (e.g. RELIANCE, TCS, INFY) and click Analyze.")
