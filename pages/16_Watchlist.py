"""Watchlist — always-on candidate ranking, ignores the regime gate.

When the tape is HOSTILE, the regime_gate downgrades every GO to WAIT
and the Picks page (correctly) shows zero entries. But the user still
wants to know which Nifty 50 names are leading — so when the tape
flips back to MIXED / TRENDING, they're not starting from scratch.

This page bypasses the regime gate at display time. It is NOT a trade
recommendation. The CMP, score, and reasons reflect the raw scorer
output; the user is shown both the raw verdict AND the tape's
overlaid recommendation.
"""
import os

import pandas as pd
import streamlit as st

from components import theme, state
from components.data_freshness import check_freshness
from components.market_data import get_live_price
from nse_backtest.data import fetch_multiple, fetch_nifty50, NIFTY50_SYMBOLS
from nse_backtest.scorer import analyze_stock
from nse_backtest.tape_monitor import assess_tape, TapeRegime

st.set_page_config(page_title="Watchlist | Trading Lab", page_icon="📡", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_nifty():
    try:
        return fetch_nifty50(start="2022-01-01")
    except Exception:
        return None


@st.cache_data(ttl=900, show_spinner=False)
def _scan_universe() -> pd.DataFrame:
    """Score every Nifty 50 name with the regime gate temporarily disabled.

    Cached 15 min — refresh is fast enough not to spam yfinance.
    """
    prev = os.environ.get("REGIME_GATE_ENABLED")
    os.environ["REGIME_GATE_ENABLED"] = "0"
    try:
        nifty = _cached_nifty()
        data = fetch_multiple(NIFTY50_SYMBOLS, start="2024-01-01")
        rows = []
        for sym, df in data.items():
            if df is None or len(df) < 60:
                continue
            try:
                r = analyze_stock(df, f"{sym}.NS", run_backtests=False, nifty_df=nifty)
                # Detect whether any safety gate would have downgraded GO.
                gates_hit = [reason for reason in r.reasons
                             if any(t in reason for t in (
                                 "MTF disconfirmation", "Liquidity too thin",
                                 "Gap-up too large", "Earnings inside",
                                 "Regime block"))]
                rows.append({
                    "Symbol": sym,
                    "Raw Score": round(r.final_score, 1),
                    "Raw Verdict": r.verdict,
                    "CMP ₹": round(r.current_price, 2),
                    "Stop ₹": round(r.stop_loss, 2),
                    "Target 1 ₹": round(r.target_1, 2),
                    "R:R": round(r.risk_reward, 2),
                    "Win %": round(r.win_probability, 1),
                    "Gates Hit": "; ".join(gates_hit[:2])[:80] or "—",
                })
            except Exception:
                continue
        df_out = pd.DataFrame(rows).sort_values("Raw Score", ascending=False).reset_index(drop=True)
        return df_out
    finally:
        if prev is None:
            os.environ.pop("REGIME_GATE_ENABLED", None)
        else:
            os.environ["REGIME_GATE_ENABLED"] = prev


# ── Header + tape banner ──
st.markdown("# 📡 Watchlist — always-on candidate ranking")
st.caption(
    "Top Nifty 50 names ranked by raw scorer output, with the regime gate **temporarily "
    "disabled**. This is NOT a trade recommendation — it shows which names would lead "
    "the moment the tape regime flips back to MIXED / TRENDING. Use it to build a "
    "shortlist now and be ready when conditions improve."
)
_nifty = _cached_nifty()
_tape = assess_tape(_nifty) if _nifty is not None else None
if _tape is not None:
    st.markdown(
        f"**Current tape**: :{'red' if _tape.regime == TapeRegime.HOSTILE else 'orange' if _tape.regime == TapeRegime.MIXED else 'green'}"
        f"[{_tape.regime}] · Nifty ₹{_tape.nifty_close:,.0f} · "
        f"60d {_tape.return_60d_pct:+.2f}% · 200EMA slope {_tape.ema_200_slope_pct_20d:+.2f}%/20d"
    )
    if _tape.regime == TapeRegime.HOSTILE:
        st.warning(
            "Tape is HOSTILE — Picks page is correctly empty. This Watchlist shows "
            "what's leading the universe so you're not starting from scratch when the "
            "regime flips. **Do not trade the names below today.**"
        )
    _freshness = check_freshness(_nifty)
    st.caption(f"● {_freshness.message}")

st.markdown("---")

# ── Scan + display ──
with st.spinner("Scoring Nifty 50…"):
    df = _scan_universe()

if df.empty:
    st.error("Watchlist scan returned no rows. Likely a yfinance issue — try again in a minute.")
else:
    top_n = st.slider("Show top N candidates", 5, 50, 20, key="watch_top_n")
    st.dataframe(df.head(top_n), use_container_width=True, hide_index=True)
    st.markdown("---")
    st.caption(
        "**Gates Hit** column shows which safety gate(s) would have downgraded the GO "
        "if the regime gate were active. 'Regime block' means the tape itself is the "
        "blocker; 'MTF disconfirmation' means weekly trend doesn't agree; etc. When "
        "those gates clear AND the tape flips, the name becomes a real GO."
    )
    st.caption(
        "🛡️ Held-out 2026 YTD with the full v2 engine returned -1.71% expectancy "
        "(64 trades, 28% win rate). This watchlist does NOT change that finding — "
        "it just prepares you for the regime that history says we need to wait for."
    )
