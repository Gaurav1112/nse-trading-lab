"""My Track Record — your own P&L curve, win rate, and rolling Sharpe.

Until your own ledger crosses ~50 closed trades, trust this page over
the walk-forward verdicts. Walk-forward is what the engine could do
historically; this is what you've actually done.
"""
import pandas as pd
import streamlit as st

from components import state, theme
from components.pnl_tracker import snapshot

st.set_page_config(page_title="Track Record | Trading Lab", page_icon="📈", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# 📈 My Track Record")
st.caption(
    "Your own closed-trade record. The walk-forward A/B tells you what the "
    "engine *could* have done historically; this page tells you what you *have* "
    "done. Trust your own data after ~50 trades — until then, this is "
    "preliminary and noise-dominated."
)

snap = snapshot(state.get_journal(), state.get_capital())

m1, m2, m3, m4 = st.columns(4)
m1.metric("Closed trades", snap.n_closed)
m2.metric("Win rate", f"{snap.win_rate_pct:.1f}%")
m3.metric("Expectancy / trade", f"{snap.expectancy_pct:+.2f}%")
m4.metric("Rolling 30d Sharpe", f"{snap.rolling_30d_sharpe:+.2f}")

st.divider()

if snap.cumulative_returns_pct:
    st.markdown("### Cumulative %  (trade-by-trade)")
    df = pd.DataFrame({"Cumulative return (%)": snap.cumulative_returns_pct})
    df.index.name = "Trade index"
    st.line_chart(
        df, x_label="Trade index (chronological)", y_label="Cumulative return (%)",
        height=380,
    )
else:
    st.info("📊 Your P&L curve will appear here after your first closed trade.")

if snap.notes:
    st.markdown("### Notes")
    for n in snap.notes:
        st.warning(n)

# ── Tear sheet (QuantConnect / PyFolio style) ──
from components.tear_sheet import build as build_tearsheet
ts = build_tearsheet(state.get_journal(), state.get_capital())
if ts.n_closed > 0:
    st.divider()
    st.markdown("## 📑 Tear sheet")
    st.caption("QuantConnect / PyFolio analog. Honest performance dashboard from your closed-trade journal.")
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Total return", f"{ts.total_return_pct:+.2f}%")
    t2.metric("Max drawdown", f"-{ts.max_drawdown_pct:.2f}pp")
    t3.metric("Sharpe (90d roll)", f"{ts.rolling_sharpe_90d:+.2f}")
    t4.metric("Lifetime Sharpe", f"{ts.sharpe_lifetime:+.2f}")

    # Monthly returns heatmap (text-based, color-blind safe)
    st.markdown("### Monthly returns (%)")
    if ts.monthly_returns:
        df_m = pd.DataFrame(
            [(k, v) for k, v in sorted(ts.monthly_returns.items())],
            columns=["Month", "Net %"],
        )

        def _hl(val):
            if val > 1: return "background-color:#0a4d28;color:#7FFFA0"
            if val > 0: return "background-color:#16361f;color:#A0FFC8"
            if val > -1: return "background-color:#3a1f1f;color:#FFB0B0"
            return "background-color:#4d0a0a;color:#FF5050"
        # pandas 2.2 renamed Styler.applymap → Styler.map (DataFrame.applymap
        # → DataFrame.map). Use map for forward compat with the version on
        # Streamlit Cloud; fall back to applymap if running an older pandas.
        styler = df_m.style
        _styler_apply = getattr(styler, "map", None) or getattr(styler, "applymap")
        st.dataframe(
            _styler_apply(_hl, subset=["Net %"]).format({"Net %": "{:+.2f}"}),
            use_container_width=True, hide_index=True,
        )

    # Equity curve (PnL %, trade by trade)
    if ts.equity_curve:
        st.markdown("### Equity curve (cumulative % over time)")
        df_eq = pd.DataFrame(
            ts.equity_curve, columns=["Trade close date", "Cumulative return (%)"],
        )
        st.line_chart(
            df_eq.set_index("Trade close date"),
            x_label="Trade close date", y_label="Cumulative return (%)",
            height=380,
        )

    if ts.notes:
        st.markdown("### Tear-sheet notes")
        for n in ts.notes:
            st.warning(n)

# ── Discipline scorecard ──
from components.discipline import assess as _disc_assess
_disc = _disc_assess(state.get_journal(), state.get_positions())
st.divider()
st.markdown("## 🛡️ Process Adherence Index")
st.caption("How well you followed the engine's rules — independent of whether the engine itself made money. Process > outcomes.")
d1, d2, d3, d4 = st.columns(4)
d1.metric("PAI", f"{_disc.process_adherence_index:.0f}/100")
d2.metric("Rule-following streak", f"{_disc.rule_following_streak_days} d")
d3.metric("Override count", _disc.override_count_total)
if _disc.override_count_total > 0:
    gap = _disc.aligned_avg_return_pct - _disc.override_avg_return_pct
    d4.metric("Override vs aligned", f"{gap:+.2f}pp",
              help="Positive = aligned trades beat overrides; you should override less.")
else:
    d4.metric("Override vs aligned", "—")
for n in _disc.notes:
    st.info(n)

st.divider()
st.caption(
    "Reference: walk-forward A/B v2 expectancy was +0.34% in HOSTILE 2025, "
    "+2.01% in MIXED 2024, +7.17% in TRENDING 2023. Held-out 2026 YTD "
    "showed -1.61% with Wave A active — strong evidence the HOSTILE-tape "
    "edge sits at or below zero."
)
