"""Decay Watch — the bagholder antidote page.

Shows every held position with today's re-score, sorted worst-first.
HOLD / TIGHTEN_STOP / EXIT badges. One-glance "what should I exit today?".
For each position, surfaces CONCRETE next-step instructions: sell at
market (EXIT), modify SL trigger (TIGHTEN_STOP), book partial at T1
(when crossed), or just keep monitoring (HOLD).
"""
import streamlit as st
import pandas as pd
from components import theme, state, cards
from nse_backtest.data import fetch_nse
from nse_backtest.position_monitor import daily_check, ReScoreAction

st.set_page_config(page_title="Decay Watch | Trading Lab", page_icon="⚠️", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# ⚠️ Decay Watch")
st.markdown("_Re-score every held position against today's market. Worst first._")

positions = state.get_positions()
if not positions:
    st.info(
        "No open positions. When you save a trade on the **Picks** page it shows here, "
        "and every time you open this page the engine re-scores every position against "
        "the latest market data and tells you whether to hold, tighten the stop, or exit."
    )
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
    qty_held = int(pos.get("qty", 0) or 0)
    sl_old = float(pos.get("stop_loss", 0) or 0)
    t1 = float(pos.get("target_1") or pos.get("target") or 0)
    t2 = float(pos.get("target_2") or 0)
    cur = float(v.current_price)
    # B4: T1-cross detection — if current price has crossed T1, surface the
    # "book 50% and trail SL to entry" action regardless of HOLD/TIGHTEN.
    t1_crossed = (t1 > 0 and cur >= t1)
    t2_crossed = (t2 > 0 and cur >= t2)

    st.markdown(
        f'<div style="border:1px solid {clr};border-radius:14px;padding:18px 20px;'
        f'margin:10px 0;background:#0D1526">'
        f'<span style="font-size:22px;font-weight:700;color:{clr}">{v.symbol}</span>'
        f'<span style="font-size:14px;color:#7A93AA;margin-left:12px">'
        f'{v.action} · re-score {v.current_rescore:.0f}/100 · held {v.bars_held} bars</span>'
        f'</div>', unsafe_allow_html=True)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Entry", f"₹{v.entry_price:,.2f}")
    m2.metric("Current", f"₹{cur:,.2f}", delta=f"{v.pnl_pct:+.1f}%",
              delta_color="normal" if v.pnl_pct >= 0 else "inverse")
    m3.metric("Stop now", f"₹{sl_old:,.2f}")
    if t1 > 0:
        dist_t1 = (t1 - cur) / cur * 100
        m4.metric("Target 1", f"₹{t1:,.2f}",
                  delta=f"{dist_t1:+.2f}% away" if not t1_crossed else "✅ CROSSED")
    else:
        m4.metric("Target 1", "—")

    st.caption(f"💡 {v.reason}")

    # B2/B3/B4 — concrete next-action card per verdict.
    if v.action == ReScoreAction.EXIT:
        st.markdown(
            cards.zerodha_sell_steps(v.symbol, cur, qty_held, reason=v.reason),
            unsafe_allow_html=True,
        )
    elif v.action == ReScoreAction.TIGHTEN_STOP and v.suggested_sl is not None:
        new_sl = float(v.suggested_sl)
        if new_sl > sl_old:
            st.markdown(
                cards.zerodha_modify_sl_steps(v.symbol, sl_old, new_sl, qty_held),
                unsafe_allow_html=True,
            )
        else:
            st.caption(
                f"Suggested SL ₹{new_sl:,.2f} is at or below current SL ₹{sl_old:,.2f} — "
                "no action needed (never widen a stop)."
            )
    elif t1_crossed and not t2_crossed:
        # B4 — even with HOLD verdict, if price crossed T1, book partial.
        half = max(1, qty_held // 2)
        st.markdown(
            f'<div class="steps" style="border-left:4px solid #00FF87;'
            f'background:#0a1d12;padding:14px 18px;border-radius:10px">'
            f'<b>🟢 T1 HIT — book partial</b>'
            f'<div style="font-size:13px;color:#A0FFC8;margin:6px 0">'
            f'Price ₹{cur:,.2f} ≥ T1 ₹{t1:,.2f}. Lock in half, trail the rest.'
            f'</div><ol>'
            f'<li>Open Kite → Search <b>{v.symbol}</b></li>'
            f'<li>If the T1 GTT (sell LIMIT ₹{t1:,.2f}, qty {half}) auto-filled, you are done.</li>'
            f'<li>Otherwise: Tap <b>S</b> → Type <b>MARKET</b> → Qty <b>{half}</b> · confirm.</li>'
            f'<li>Modify the SL-M trigger up to entry <b>₹{v.entry_price:,.2f}</b> '
            f'(breakeven trail on the remaining {qty_held - half} shares).</li>'
            '</ol></div>',
            unsafe_allow_html=True,
        )
    elif t2_crossed:
        st.markdown(
            f'<div class="steps" style="border-left:4px solid #00FF87;'
            f'background:#0a1d12;padding:14px 18px;border-radius:10px">'
            f'<b>🟢 T2 HIT — close the rest</b>'
            f'<div style="font-size:13px;color:#A0FFC8;margin:6px 0">'
            f'Price ₹{cur:,.2f} ≥ T2 ₹{t2:,.2f}. Ride is done.'
            f'</div><ol>'
            f'<li>Open Kite → Search <b>{v.symbol}</b></li>'
            f'<li>If the T2 GTT auto-filled, you are done.</li>'
            f'<li>Otherwise: Tap <b>S</b> → Type <b>MARKET</b> → '
            f'Qty <b>remaining</b> · confirm.</li>'
            f'<li>Cancel any leftover SL-M / GTT sell orders on {v.symbol}.</li>'
            '</ol></div>',
            unsafe_allow_html=True,
        )
    # HOLD with no T-crossings: no extra card — the caption already says it.

    st.markdown("---")
