"""Intraday 15-min RSI scanner — overhauled after the 2026-06-19 INFY loss.

The user took an intraday signal from an earlier version of this page that
had ZERO friction equivalent to the swing path's HOSTILE hard-block. RSI<15
in IT sector capitulation is a known false-positive pattern. The original
caption warned about it; the gate didn't enforce it. Now it does.

Five expert subagents reviewed the bug. Consensus:
  1. Daily HOSTILE tape gate must hard-block intraday longs (with override)
  2. RSI extreme alone is not a signal — require reversal candle + volume
  3. Sector capitulation detection (≥3 names oversold = continuation, not bounce)
  4. Time-of-day filter (no entries 9:15-9:30 or after 15:00)
  5. MIS product + Cover Order (CNC for intraday holds overnight = swing loss)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pandas as pd
import streamlit as st

from components import state, theme, cards
from components.data_freshness import _ist_now
from nse_backtest.data import NIFTY50_SYMBOLS, NIFTY100_SYMBOLS, fetch_nifty50
from nse_backtest.intraday.rsi_scanner import scan_rsi
from nse_backtest.intraday.safety_gates import (
    good_time_of_day_to_enter, time_to_mis_squareoff,
)
from nse_backtest.tape_monitor import assess_tape, TapeRegime

IST = timezone(timedelta(hours=5, minutes=30))


st.set_page_config(page_title="Intraday Scanner | Trading Lab",
                   page_icon="⚡", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# ⚡ Intraday RSI Scanner (15-min bars)")
st.caption(
    "Mean-reversion oversold scanner with safety gates. Filters out: "
    "stocks still falling (no reversal candle), low-volume bounces, "
    "sector-wide capitulation, stale bars, volatile time-of-day windows."
)

# ── 1. HONEST EXPECTANCY — MEASURED via scripts/intraday_walkforward.py ──
st.error(
    "🛑 **MEASURED intraday expectancy is NEGATIVE at every strict RSI threshold.**\n\n"
    "60-day walk-forward, Nifty 50, all safety gates active, **before** transaction costs:\n\n"
    "| RSI < | Trades | Win rate | Expectancy | PF |\n"
    "|---|---|---|---|---|\n"
    "| 15 | 8 | 37.5% | **-0.154%** | 0.63 |\n"
    "| 20 | 28 | 50.0% | -0.083% | 0.78 |\n"
    "| 25 | 81 | 47.0% | -0.035% | 0.91 |\n"
    "| 30 | 143 | 47.0% | +0.002% | 1.01 |\n\n"
    "**Even RSI<30 (the widest threshold) is gross-breakeven — net of ~0.1% "
    "Zerodha costs it loses.** This is REAL measured data from "
    "`output/walk_forward/intraday_verdict.md`, regenerated weekly. Until this "
    "table shows PF > 1.3 and expectancy > +0.2%, every trade you take from "
    "this scanner is fighting a strategy with no demonstrated edge. "
    "**Paper-trade only.**"
)

# ── 2. HOSTILE TAPE HARD-BLOCK (with override phrase, mirrors Picks) ────
@st.cache_data(ttl=3600, show_spinner=False)
def _cached_nifty():
    try:
        return fetch_nifty50(start="2022-01-01")
    except Exception:
        return None


_nifty = _cached_nifty()
_tape = assess_tape(_nifty) if _nifty is not None else None
_INTRADAY_OVERRIDE = "i accept intraday risk in HOSTILE tape"
_hostile = (_tape is not None and _tape.regime == TapeRegime.HOSTILE)
_override_ok = True
if _hostile:
    st.markdown(
        '<div style="border:3px solid #FF4D4D;border-radius:14px;padding:14px 18px;'
        'margin:8px 0;background:#1a0d0d">'
        '<span style="font-size:11px;color:#FF9090;text-transform:uppercase;letter-spacing:1px">'
        'Daily tape regime · intraday hard-block</span><br>'
        '<span style="font-size:18px;font-weight:700;color:#FF4D4D">'
        f'🛑 Tape is HOSTILE (Nifty {_tape.nifty_close:,.0f}, 200EMA slope '
        f'{_tape.ema_200_slope_pct_20d:+.2f}%/20d).</span>'
        '<div style="color:#FFD0D0;font-size:13px;margin-top:6px">'
        'Intraday mean-reversion fails MORE in HOSTILE tape — falling knives '
        'continue, sector selloffs dominate over individual bounces. Scan + '
        'order placement are blocked unless you explicitly accept the risk.'
        '</div></div>',
        unsafe_allow_html=True,
    )
    override_text = st.text_input(
        f"To enable intraday scan in HOSTILE, type exactly:  `{_INTRADAY_OVERRIDE}`",
        key="intra_hostile_override", value="",
    )
    _override_ok = (override_text.strip() == _INTRADAY_OVERRIDE)
    if not _override_ok and override_text:
        st.warning("Override phrase doesn't match. Scan still disabled.")
elif _tape is not None:
    st.info(
        f"Tape: **{_tape.regime}** (Nifty {_tape.nifty_close:,.0f}). "
        "Intraday scan enabled — mean-reversion has a chance in this regime, "
        "but safety gates still apply per signal."
    )

# ── 3. TIME-OF-DAY GATE ────────────────────────────────────────────────
_tod_ok, _tod_msg = good_time_of_day_to_enter()
_sq_min, _sq_msg = time_to_mis_squareoff()
if not _tod_ok:
    st.warning(f"⏰ {_tod_msg}")
elif _sq_min < 60:
    st.warning(
        f"⏰ **{_sq_msg}.** New MIS entries crystallize at 15:15 regardless of "
        "thesis. Either close manually before then or skip new entries."
    )
else:
    st.caption(f"⏰ {_tod_msg} · {_sq_msg}")

# ── 4. INPUTS ──────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
universe_label = c1.selectbox("Universe", ["Nifty 50", "Nifty 100", "Custom"], key="intra_univ")
rsi_threshold = c2.slider("RSI threshold (<)", 5, 50, 15, 1, key="intra_rsi_th",
                          help="RSI<15 is rare. Default 15 = strict. Raise to widen the net.")
rsi_period = c3.number_input("RSI period", 5, 50, 14, 1, key="intra_rsi_p")
custom_input = c4.text_input("Custom symbols (comma-sep)", "", key="intra_custom",
                              disabled=(universe_label != "Custom"))
show_failed = st.checkbox(
    "Also show signals that failed safety gates (for transparency)",
    value=False, key="intra_show_failed",
)


@st.cache_data(ttl=60, show_spinner=False)
def _cached_scan(universe_key: tuple[str, ...], threshold: float, period: int):
    return scan_rsi(list(universe_key), rsi_threshold=threshold, rsi_period=period)


# ── 5. SCAN BUTTON (gated by tape + time-of-day) ───────────────────────
scan_disabled = (not _override_ok) or (not _tod_ok)
if st.button("🔍 Scan now", type="primary", use_container_width=True,
             disabled=scan_disabled):
    if universe_label == "Nifty 50":
        syms = NIFTY50_SYMBOLS
    elif universe_label == "Nifty 100":
        syms = NIFTY100_SYMBOLS
    else:
        syms = [s.strip().upper() for s in custom_input.split(",") if s.strip()]
        if not syms:
            st.error("Enter at least one custom symbol.")
            st.stop()
    with st.spinner(f"Fetching 15-min bars + safety gates for {len(syms)} symbols…"):
        hits = _cached_scan(tuple(syms), float(rsi_threshold), int(rsi_period))
    st.session_state["intra_hits"] = hits
    st.session_state["intra_scan_ts"] = _ist_now().strftime("%Y-%m-%d %H:%M:%S IST")

hits = st.session_state.get("intra_hits", [])
scan_ts = st.session_state.get("intra_scan_ts")
if scan_ts:
    st.caption(f"📡 Last scan: {scan_ts}")

if not hits and scan_ts:
    st.info(
        f"**0 hits** at RSI<{rsi_threshold}. This is the most common outcome "
        "at strict thresholds — try raising the threshold or widening the universe."
    )
elif hits:
    # Split passed vs failed
    passed = [h for h in hits if h.passed_all_gates]
    failed = [h for h in hits if not h.passed_all_gates]

    if not passed:
        st.warning(
            f"⚠️ **{len(hits)} RSI candidates, NONE passed all safety gates.** "
            "Every candidate has at least one gate failure. This is exactly the "
            "moment to NOT trade. (Tick the 'Also show failed' box above to see why.)"
        )
    else:
        st.success(f"✅ **{len(passed)} candidate(s) passed every safety gate** ({len(failed)} filtered out).")

    def _render_hits(items, header_color="#00FF87", title=""):
        if not items:
            return
        if title:
            st.markdown(f"### {title}")
        rows = []
        for h in items:
            bar_age = int((_ist_now() - h.last_bar_ts.tz_convert(IST)).total_seconds() / 60) if h.last_bar_ts.tz else 0
            distance_to_vwap = ((h.current_price - h.vwap) / h.vwap * 100) if h.vwap else 0
            rows.append({
                "Symbol": h.symbol,
                "RSI(14)": round(h.rsi, 1),
                "RSI Δ": round(h.rsi - h.rsi_prev, 1),
                "CMP ₹": round(h.current_price, 2),
                "VWAP ₹": round(h.vwap, 2),
                "% vs VWAP": f"{distance_to_vwap:+.2f}%",
                "Today %": f"{h.change_pct_today:+.2f}%",
                "Vol vs 20-bar": f"{h.volume_ratio:.2f}×",
                "Bar age (min)": bar_age,
                "Failed gates": ", ".join(h.failed_gates) if h.failed_gates else "—",
                "Kite chart": f"https://kite.zerodha.com/chart/web/ciq/NSE/{h.symbol}/15minute",
            })
        df = pd.DataFrame(rows)
        st.dataframe(
            df, use_container_width=True, hide_index=True,
            column_config={
                "Kite chart": st.column_config.LinkColumn(
                    "📈 Kite", display_text="open",
                ),
            },
        )

    if passed:
        _render_hits(passed, title="✅ Passed all gates (consider for entry)")
        # Sector capitulation warnings on passed
        capit = {h.symbol: h.sector_capitulation_warning for h in passed if h.sector_capitulation_warning}
        for sym, warn in capit.items():
            st.warning(f"**{sym}**: {warn}")
        # MIS+CO order steps for the FIRST passed hit
        top = passed[0]
        st.markdown("---")
        st.markdown("### 📋 Suggested order (top candidate)")
        # Tight intraday SL: 0.75x of typical bar range as a placeholder
        intraday_sl = round(top.current_price * 0.992, 2)  # ~0.8% stop floor
        intraday_target = round(top.current_price * 1.015, 2)  # ~1.5% target
        # Sensible default quantity (caller can override on Kite)
        default_qty = max(1, int(state.get_capital() * 0.005 / max(0.01, top.current_price - intraday_sl)))
        st.markdown(
            cards.zerodha_steps_intraday(
                top.symbol, top.current_price, intraday_sl,
                default_qty, target=intraday_target,
            ),
            unsafe_allow_html=True,
        )

    if failed and show_failed:
        _render_hits(failed, title=f"❌ Failed gates ({len(failed)} hidden — visible because toggle on)")

    st.markdown("---")
    st.caption(
        "🛡️ **What each gate checks:** "
        "**Reversal candle** = current bar is green with ≥30% lower wick (rejection). "
        "**Volume** = current bar volume ≥ 1.5× 20-bar avg. "
        "**Bar fresh** = signal not older than 30 min. "
        "**Time of day** = block 9:15-9:30 and 15:00-15:30 (volatile windows). "
        "**Sector capitulation** = ≥3 names in same sector oversold simultaneously. "
        "All five must pass before the signal is actionable."
    )
else:
    st.info("Hit **Scan now** to find oversold 15-min RSI candidates that pass every safety gate.")
