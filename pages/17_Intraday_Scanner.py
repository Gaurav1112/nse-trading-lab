"""Intraday 15-min RSI scanner — oversold finder for mean-reversion entries.

This page is a SEPARATE workflow from the swing engine. Picks here are NOT
covered by the walk-forward A/B (which is daily-bar swing). Treat it as a
watchlist for further investigation, not as a backtested strategy.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pandas as pd
import streamlit as st

from components import state, theme
from components.data_freshness import _ist_now
from nse_backtest.data import NIFTY50_SYMBOLS, NIFTY100_SYMBOLS
from nse_backtest.intraday.rsi_scanner import scan_rsi

IST = timezone(timedelta(hours=5, minutes=30))


st.set_page_config(page_title="Intraday Scanner | Trading Lab",
                   page_icon="⚡", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# ⚡ Intraday RSI Scanner (15-min bars)")
st.caption(
    "Mean-reversion oversold scanner. Lists stocks where the 14-period RSI "
    "on 15-minute bars is below your threshold (default 15)."
)

# ── Honest scope disclaimer (always visible) ─────────────────────────────
st.warning(
    "⚠️ **Scope disclaimer.** This scanner is independent of the swing engine "
    "and is NOT validated by the walk-forward A/B. The walk-forward expectancy "
    "(+7.5% TRENDING / +2% MIXED / -1.78% HOSTILE) does NOT apply here — "
    "different timeframe, different mechanism. Intraday mean-reversion at "
    "RSI extremes can work, but you must paper-trade it yourself before "
    "deploying real capital."
)

# ── Market hours check ──────────────────────────────────────────────────
now_ist = _ist_now()
market_open_now = (
    now_ist.weekday() < 5 and
    (now_ist.hour, now_ist.minute) >= (9, 15) and
    (now_ist.hour, now_ist.minute) <= (15, 30)
)
if not market_open_now:
    st.error(
        f"🛑 Market is **CLOSED** right now ({now_ist.strftime('%a %H:%M IST')}). "
        "Intraday RSI signals only make sense during market hours (Mon-Fri 09:15-15:30 IST). "
        "Run the scanner once the market opens."
    )

# ── Inputs ─────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
universe_label = c1.selectbox(
    "Universe", ["Nifty 50", "Nifty 100", "Custom"], key="intra_univ",
    help="Nifty 50 = fastest scan (~5-10s). Nifty 100 doubles the API load.",
)
rsi_threshold = c2.slider(
    "RSI threshold (<)", min_value=5, max_value=50, value=15, step=1,
    key="intra_rsi_th",
    help=(
        "Standard 'oversold' is RSI < 30. RSI < 15 is rare — most days "
        "you'll see 0 hits. Lower the threshold to widen the net; raise it "
        "to tighten."
    ),
)
rsi_period = c3.number_input(
    "RSI period", value=14, min_value=5, max_value=50, step=1, key="intra_rsi_p",
    help="14 is Wilder's original; standard for most platforms.",
)
custom_input = c4.text_input(
    "Custom symbols (comma-sep)", value="", key="intra_custom",
    disabled=(universe_label != "Custom"),
    help="e.g., RELIANCE, HDFCBANK, TCS",
)


@st.cache_data(ttl=60, show_spinner=False)
def _cached_scan(universe_key: tuple[str, ...], threshold: float, period: int):
    """Cached so re-pressing Scan within 60s doesn't re-hit yfinance."""
    return scan_rsi(list(universe_key), rsi_threshold=threshold,
                    rsi_period=period)


# ── Scan button + results ──────────────────────────────────────────────
if st.button("🔍 Scan now", type="primary", use_container_width=True,
             disabled=(not market_open_now)):
    if universe_label == "Nifty 50":
        syms = NIFTY50_SYMBOLS
    elif universe_label == "Nifty 100":
        syms = NIFTY100_SYMBOLS
    else:
        syms = [s.strip().upper() for s in custom_input.split(",") if s.strip()]
        if not syms:
            st.error("Enter at least one custom symbol.")
            st.stop()
    with st.spinner(f"Fetching 15-min bars for {len(syms)} symbols…"):
        hits = _cached_scan(tuple(syms), float(rsi_threshold), int(rsi_period))
    st.session_state["intra_hits"] = hits
    st.session_state["intra_scan_ts"] = now_ist.strftime("%Y-%m-%d %H:%M:%S IST")

hits = st.session_state.get("intra_hits", [])
scan_ts = st.session_state.get("intra_scan_ts")
if scan_ts:
    st.caption(f"📡 Last scan: {scan_ts}")

if not hits and scan_ts:
    st.info(
        f"**0 stocks** in the {universe_label} universe currently have RSI(14) "
        f"below {rsi_threshold} on 15-min bars. This is the most common "
        "outcome at strict thresholds — most days nothing fires. Try raising "
        "the threshold (e.g., 30 for a wider net) if you want more candidates."
    )
elif hits:
    rows = []
    for h in hits:
        # Wider freshness gap = stale signal
        bar_age_min = (now_ist - h.last_bar_ts.tz_convert(IST)).total_seconds() / 60
        rows.append({
            "Symbol": h.symbol,
            "RSI(14)": round(h.rsi, 1),
            "CMP ₹": round(h.current_price, 2),
            "Today change %": f"{h.change_pct_today:+.2f}%",
            "Volume (15m)": f"{h.volume:,}",
            "Bars today": h.bars_in_session,
            "Bar age (min)": int(bar_age_min),
            "Kite link": f"https://kite.zerodha.com/chart/web/ciq/NSE/{h.symbol}/15minute",
        })
    df = pd.DataFrame(rows)

    def _hl_rsi(v):
        if v < 10:  return "background-color:#4d0a0a;color:#FF5050;font-weight:bold"
        if v < 20:  return "background-color:#3a1f1f;color:#FFB0B0"
        return ""

    st.markdown(f"### {len(hits)} hits — sorted by RSI (most oversold first)")
    st.dataframe(
        df.style.applymap(_hl_rsi, subset=["RSI(14)"]),
        use_container_width=True, hide_index=True,
        column_config={
            "Kite link": st.column_config.LinkColumn(
                "Kite chart (15m)", help="Open the 15-min chart on Kite",
                display_text="📈 chart",
            ),
        },
    )

    st.markdown("---")
    st.caption(
        "💡 **How to use this responsibly.** RSI extremes are necessary but "
        "not sufficient for a long entry. Before buying, also check: "
        "(a) the 5-min or 1-min chart for a real reversal candle, "
        "(b) volume confirmation on the bounce, "
        "(c) overall tape regime — the daily Tape Monitor shows "
        "HOSTILE today, which means even intraday mean-reversion bounces "
        "tend to fail more often. "
        "Suggested workflow: paper-trade 20 of these signals first, "
        "check your hit rate, then size with Kelly on validated edge."
    )
else:
    st.info("Hit **Scan now** to find oversold 15-min RSI candidates.")
