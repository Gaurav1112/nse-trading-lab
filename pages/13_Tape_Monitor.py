"""Tape Monitor — see what regime we're in today, and the historical context."""
import streamlit as st
import pandas as pd
from components import theme, state
from components.data_freshness import check_freshness
from nse_backtest.data import fetch_nifty50
from nse_backtest.tape_monitor import assess_tape, TapeRegime

st.set_page_config(page_title="Tape Monitor | Trading Lab", page_icon="🌡️", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# 🌡️ Tape Monitor")
st.markdown("_What regime is the market in today? Trade accordingly._")

@st.cache_data(ttl=3600)
def _load_nifty():
    return fetch_nifty50(start="2022-01-01")

try:
    nifty = _load_nifty()
except Exception as e:
    st.error(f"Failed to fetch Nifty 50: {e}")
    st.stop()

assessment = assess_tape(nifty)
if assessment is None:
    st.error("Insufficient Nifty 50 data to classify regime.")
    st.stop()

st.markdown(
    f'<div style="border:2px solid {assessment.color};border-radius:18px;padding:24px 28px;'
    f'margin:14px 0;background:#0D1526">'
    f'<span style="font-size:48px;font-weight:800;color:{assessment.color}">{assessment.regime}</span>'
    f'<div style="margin-top:12px;color:#C9D5E0;font-size:16px;line-height:1.5">'
    f'{assessment.recommendation}</div>'
    f'</div>', unsafe_allow_html=True)

# ── Data freshness indicator (Rohan's integrity guard) ────────
_freshness = check_freshness(nifty)
st.markdown(
    f"<div style='font-size:12px;color:#7A93AA;margin:4px 0 10px 0'>"
    f"<span style='color:{_freshness.color};font-weight:700'>●</span> "
    f"Last Nifty bar: <b>{_freshness.last_bar_date_str}</b> · {_freshness.status} · "
    f"{_freshness.message}</div>",
    unsafe_allow_html=True,
)
# ── end freshness ─────────────────────────────────────────────

m1, m2, m3, m4 = st.columns(4)
m1.metric("Nifty 50", f"₹{assessment.nifty_close:,.0f}")
m2.metric("60d return", f"{assessment.return_60d_pct:+.1f}%")
m3.metric("200-EMA slope (20d)", f"{assessment.ema_200_slope_pct_20d:+.2f}%")
m4.metric("Above 200-EMA", "Yes" if assessment.above_200ema else "No")

st.markdown("---")
st.markdown("## Historical regime expectancy (from walk-forward replay)")
st.dataframe(pd.DataFrame([
    {"Window": "2023 (strong bull)", "Regime": "TRENDING", "v1 expectancy": "+7.55%", "v1 PF": 7.94, "v1 WR": "75.7%"},
    {"Window": "2024 (mixed)",       "Regime": "MIXED",    "v1 expectancy": "+2.25%", "v1 PF": 1.64, "v1 WR": "45.9%"},
    {"Window": "2025 (current)",     "Regime": "HOSTILE",  "v1 expectancy": "+0.05%", "v1 PF": 1.01, "v1 WR": "42.6%"},
]), use_container_width=True, hide_index=True)

st.markdown("---")
st.markdown("## Nifty 50 — last 250 sessions")
chart_df = nifty.tail(250)[["Close"]].copy()
chart_df["50 EMA"] = chart_df["Close"].ewm(span=50, adjust=False).mean()
chart_df["200 EMA"] = chart_df["Close"].ewm(span=200, adjust=False).mean()
chart_df.index.name = "Date"
chart_df = chart_df.rename(columns={"Close": "Nifty 50 close (₹)"})
st.line_chart(
    chart_df, x_label="Date", y_label="Nifty 50 close (₹)",
    height=420,
)
st.caption(
    "Lines: actual close (blue), 50-day EMA (orange), 200-day EMA (red). "
    "The regime classifier above is computed from the gap between these "
    "EMAs and the slope of the 200-EMA."
)

st.markdown("---")
st.markdown("## 🎯 Win-probability calibration")
try:
    from nse_backtest.model.calibrator import _load_calibrator, _ceiling_from_buckets
    cal = _load_calibrator()
    if cal:
        st.caption(
            f"**Calibrator v{cal.get('version', '?')}** — fit on {cal['n_trades']} "
            f"walk-forward trades, trained {cal.get('fit_date', '?')[:10]}. "
            "Enable v3 via `NSE_SCORER_ENGINE=v3`."
        )
        ceiling = _ceiling_from_buckets(cal.get("bucket_stats", {}))
        if ceiling is not None:
            st.caption(
                f"Runtime ceiling cap: max calibrated win probability is "
                f"**{ceiling * 100:.1f}%** (highest actual win rate in any "
                f"reliable training bucket). The engine will never claim "
                "more confidence than the data supports."
            )
        brier = cal.get("held_out_brier")
        if isinstance(brier, dict) and brier:
            rows = " | ".join(f"**{y}**: {v:.3f}" for y, v in sorted(brier.items()))
            st.caption(
                f"Held-out Brier per evaluation year — {rows}. "
                "Reference: a constant-baseline predictor scores ~0.25. "
                "Lower is better. If isotonic ≥ baseline, the calibrator "
                "adds no information beyond the historical win rate — "
                "use v2 (default) instead of v3."
            )
        if cal.get("notes"):
            with st.expander("Calibrator notes & design decisions"):
                st.write(cal["notes"])
    else:
        st.caption(
            "Calibrator not yet trained. Run "
            "`PYTHONPATH=. python3 scripts/train_calibration.py`."
        )
except Exception as e:
    st.caption(f"Calibrator status: error ({e})")
