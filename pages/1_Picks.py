import streamlit as st
import pandas as pd
from datetime import datetime
from components import theme, state, cards
from components.market_data import get_live_price
from nse_backtest.data import fetch_multiple, NIFTY50_SYMBOLS, NIFTY100_SYMBOLS
from nse_backtest.trading_modes import analyze_swing
from nse_backtest.sample_data import trending_stock, volatile_midcap, sideways_stock

st.set_page_config(page_title="Today's Picks | Trading Lab", page_icon="🎯", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# 🎯 Today's Best Stocks")
st.markdown("_Top actionable NSE stocks right now. Scored, ranked, one-tap position entry._")

# ── Tape regime banner ─────────────────────────────────────────
from nse_backtest.tape_monitor import assess_tape, TapeRegime
from nse_backtest.data import fetch_nifty50

@st.cache_data(ttl=3600)  # refresh hourly
def _cached_tape():
    try:
        nifty_df = fetch_nifty50(start="2022-01-01")
        return assess_tape(nifty_df)
    except Exception as e:
        return None

_tape = _cached_tape()
if _tape is not None:
    st.markdown(
        f'<div style="border:1px solid {_tape.color};border-radius:14px;padding:14px 18px;'
        f'margin:8px 0 16px 0;background:#0D1526">'
        f'<span style="font-size:11px;color:#5A7390;text-transform:uppercase;letter-spacing:1px">'
        f'Tape Regime · Nifty 50 ₹{_tape.nifty_close:,.0f}</span><br>'
        f'<span style="font-size:24px;font-weight:700;color:{_tape.color}">{_tape.regime}</span>'
        f'<span style="font-size:13px;color:#7A93AA;margin-left:12px">'
        f'60d {_tape.return_60d_pct:+.1f}% · 200EMA slope {_tape.ema_200_slope_pct_20d:+.2f}%/20d'
        f'</span>'
        f'<div style="margin-top:8px;color:#C9D5E0;font-size:13px;line-height:1.4">'
        f'{_tape.recommendation}</div>'
        f'</div>', unsafe_allow_html=True)
else:
    st.caption("⚠️ Tape regime unavailable (Nifty data fetch failed)")
# ── end banner ─────────────────────────────────────────────────

pc1, pc2, pc3 = st.columns([2, 2, 1])
scan_univ = pc1.selectbox("Universe", ["Nifty 100 (Live)", "Nifty 50 (Live)", "Demo (Instant)"], key="picks_univ")
min_score = pc2.slider("Min Score", 40, 90, 65, key="picks_min_score")
max_picks = pc3.selectbox("Max picks", [3, 4, 5], key="picks_max")

if st.button("🔍  Find Today's Best Stocks", type="primary", use_container_width=True, key="picks_scan_btn"):
    with st.spinner("Scanning market…"):
        try:
            if "Demo" in scan_univ:
                raw = {"DEMO_UP": trending_stock(), "DEMO_MID": volatile_midcap(),
                       "DEMO_FLAT": sideways_stock(), "DEMO_UP2": trending_stock()}
            elif "100" in scan_univ:
                raw = fetch_multiple(NIFTY100_SYMBOLS, start="2021-01-01")
            else:
                raw = fetch_multiple(NIFTY50_SYMBOLS, start="2021-01-01")

            picks = []
            cap, risk = state.get_capital(), state.get_risk_pct()
            for sym, sdf in raw.items():
                if len(sdf) < 60:
                    continue
                avg_vol = sdf["Volume"].rolling(20).mean().iloc[-1]
                if pd.notna(avg_vol) and avg_vol < 100_000:
                    continue
                try:
                    setup = analyze_swing(sdf, sym, cap, risk)
                    if setup and setup.signal == "BUY" and setup.score >= min_score:
                        picks.append((setup.score, sym, setup))
                except Exception:
                    pass
            picks.sort(reverse=True)
            st.session_state["today_picks"] = picks[:max_picks]
        except Exception as e:
            st.error(f"Scan failed: {e}")

picks = st.session_state.get("today_picks", [])
if not picks:
    st.info("Hit **Find Today's Best Stocks** to scan the market.")
else:
    st.markdown(f"### ✅ {len(picks)} stocks cleared the {min_score}+ score filter")
    for rank, (sc, sym, s) in enumerate(picks, 1):
        # Apply live-price ratio correction — yfinance auto_adjust=True can return
        # split-adjusted prices that diverge from the actual market price by >5%.
        if not sym.startswith("DEMO"):
            _live, _prev_close = get_live_price(sym)
            if _live and s.entry_price > 0 and abs(_live / s.entry_price - 1.0) > 0.05:
                _r = _live / s.entry_price
                s.entry_price = round(_live, 2)
                s.stop_loss = round(s.stop_loss * _r, 2)
                s.target_1 = round(s.target_1 * _r, 2)
                s.position_value = s.suggested_qty * s.entry_price
                s.max_loss = (s.entry_price - s.stop_loss) * s.suggested_qty

        clr = "#00FF87" if sc >= 65 else "#FFB800"
        st.markdown(
            f'<div style="border:1px solid {clr};border-radius:14px;padding:18px 20px;'
            f'margin:10px 0;background:#0D1526">'
            f'<span style="font-size:11px;color:#5A7390;text-transform:uppercase">#{rank} Pick</span><br>'
            f'<span style="font-size:26px;font-weight:700;color:{clr}">{sym}</span>'
            f'<span style="font-size:13px;color:#7A93AA;margin-left:12px">Score {sc:.0f}/100</span>'
            f'</div>', unsafe_allow_html=True)
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Entry", f"₹{s.entry_price:,.0f}")
        sl_pct = (s.entry_price - s.stop_loss) / s.entry_price * 100
        m2.metric("Stop Loss", f"₹{s.stop_loss:,.0f}", f"-{sl_pct:.1f}%")
        t1_pct = (s.target_1 - s.entry_price) / s.entry_price * 100
        m3.metric("Target 1", f"₹{s.target_1:,.0f}", f"+{t1_pct:.1f}%")
        m4.metric("R:R", f"{s.risk_reward:.1f}:1")
        m5.metric("Win %", f"{s.win_probability:.0f}%")
        if s.suggested_qty > 0:
            st.caption(f"Suggested: **{s.suggested_qty} shares** = ₹{s.position_value:,.0f} | Max loss ₹{s.max_loss:,.0f}")
        with st.expander("📋 Why this stock?"):
            for r in s.reasons[:8]:
                st.caption(f"• {r}")
        st.markdown(cards.zerodha_steps(sym, s.entry_price, s.stop_loss, s.suggested_qty), unsafe_allow_html=True)
        with st.expander(f"✅ I bought {sym} — record position"):
            bf1, bf2, bf3 = st.columns(3)
            bought_price = bf1.number_input("Buy price ₹", value=float(s.entry_price), min_value=0.01, format="%.2f", key=f"bp_{sym}_{rank}")
            bought_qty = bf2.number_input("Shares bought", value=max(s.suggested_qty, 1), min_value=1, key=f"bq_{sym}_{rank}")
            sl_set = bf3.number_input("Your SL ₹", value=float(s.stop_loss), min_value=0.01, format="%.2f", key=f"bsl_{sym}_{rank}")
            if st.button(f"💾 Save {sym}", key=f"save_{sym}_{rank}", use_container_width=True):
                state.add_position({"symbol": sym, "buy_price": bought_price, "qty": bought_qty,
                                     "stop_loss": sl_set, "target": s.target_1,
                                     "date": datetime.now().strftime("%Y-%m-%d"),
                                     "invested": bought_price * bought_qty})
                st.success(f"✅ {sym} saved — {bought_qty} shares @ ₹{bought_price:.2f}")
        st.markdown("---")

positions = state.get_positions()
if positions:
    st.markdown("## 💼 My Open Positions")
    total_invested = sum(p["invested"] for p in positions)
    st.caption(f"Total deployed: ₹{total_invested:,.0f}")
    for i, p in enumerate(positions):
        c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
        c1.markdown(f"**{p['symbol']}** — {p['qty']} shares @ ₹{p['buy_price']:.2f}")
        c1.caption(f"SL ₹{p['stop_loss']:.2f} | Target ₹{p['target']:.2f} | {p['date']}")
        c2.metric("Invested", f"₹{p['invested']:,.0f}")
        c3.metric("Max Loss", f"₹{(p['buy_price'] - p['stop_loss']) * p['qty']:,.0f}")
        with c4:
            if st.button("🗑️", key=f"del_{p['symbol']}_{i}"):
                state.remove_position(i); st.rerun()
