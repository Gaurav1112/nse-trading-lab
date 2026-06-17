"""Trade Replay — post-mortem viewer for closed trades.

For each closed trade, show what the engine said at entry (score,
calibrated win prob, R:R) vs what actually happened (exit reason,
realized return). The goal is to surface engine mis-calls so the
user can tune their judgment overlay.
"""
import pandas as pd
import streamlit as st

from components import state, theme

st.set_page_config(page_title="Trade Replay | Trading Lab", page_icon="🎞️", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# 🎞️ Trade Replay")
st.caption(
    "Per-trade post-mortem: what the engine said at entry vs what happened. "
    "Helps you spot the trades where your own judgment should have over-ridden "
    "the engine (and vice versa). Use this to tune your overlay."
)

journal = state.get_journal()
if not journal:
    st.info("No closed trades yet. Replay will appear here after your first trade closes.")
    st.stop()


def _f(t: dict, *keys, default=None):
    for k in keys:
        v = t.get(k)
        if v not in (None, ""):
            return v
    return default


rows = []
for t in journal:
    pred_score = _f(t, "score_at_entry", "score", default=None)
    pred_p = _f(t, "win_prob_at_entry", "win_probability", "win_prob", default=None)
    pred_rr = _f(t, "rr_at_entry", "risk_reward", default=None)
    actual_ret = _f(t, "net_return_pct", "return_pct", "pct", default=None)
    exit_reason = _f(t, "exit_reason", default="—")
    tape = _f(t, "tape_at_entry", default="—")
    correct = None
    if isinstance(actual_ret, (int, float)):
        if pred_score and pred_score >= 65 and actual_ret > 0:
            correct = "✅ engine right (GO → win)"
        elif pred_score and pred_score >= 65 and actual_ret < 0:
            correct = "❌ engine wrong (GO → loss)"
        elif pred_score and pred_score < 45 and actual_ret < 0:
            correct = "✅ engine right (AVOID → loss)"
        elif pred_score and pred_score < 45 and actual_ret > 0:
            correct = "⚠️ engine missed (AVOID → win)"
    # R6 — engine-vs-user override tracking
    overrode = bool(t.get("opened_against_engine"))
    rows.append({
        "Symbol": _f(t, "symbol", default="?"),
        "Entry date": str(_f(t, "entry_date", "date", default=""))[:10],
        "Exit date": str(_f(t, "closed_date", "exit_date", default=""))[:10],
        "Tape@entry": tape,
        "Score@entry": pred_score if pred_score is not None else "—",
        "Win%@entry": f"{pred_p:.0f}%" if isinstance(pred_p, (int, float)) else "—",
        "R:R@entry": f"{pred_rr:.2f}" if isinstance(pred_rr, (int, float)) else "—",
        "Actual net%": f"{actual_ret:+.2f}%" if isinstance(actual_ret, (int, float)) else "—",
        "Exit reason": exit_reason,
        "Verdict": correct or "—",
        "Overrode engine": "🛑 YES" if overrode else "—",
    })

df = pd.DataFrame(rows)
st.markdown(f"### {len(df)} closed trades")
st.dataframe(df, use_container_width=True, hide_index=True)

# Aggregate engine quality stats
hits = sum(1 for r in rows if "engine right" in str(r["Verdict"]))
misses = sum(1 for r in rows if "engine wrong" in str(r["Verdict"]) or "missed" in str(r["Verdict"]))
if hits + misses > 0:
    st.markdown("### Engine quality")
    c1, c2, c3 = st.columns(3)
    c1.metric("Trades scored", hits + misses)
    c2.metric("Engine right", hits)
    c3.metric("Engine wrong", misses,
              delta=f"{hits/(hits+misses)*100:.1f}% hit rate" if hits+misses else "—")
    st.caption(
        "An honest engine should be right roughly 60-70% of the time on "
        "high-conviction GO calls in TRENDING tape, ~50% in MIXED, and "
        "near-random in HOSTILE. Material deviation in either direction "
        "deserves investigation."
    )

    # R6 — discipline scorecard: how often did the user override the engine?
    overrides = [r for r in rows if r.get("Overrode engine", "") == "🛑 YES"]
    if overrides:
        st.markdown("### Discipline scorecard")
        n_over = len(overrides)
        override_rets = [
            float(str(r["Actual net%"]).rstrip("%"))
            for r in overrides
            if str(r["Actual net%"]).rstrip("%").lstrip("+-").replace(".", "").isdigit()
        ]
        avg_over = sum(override_rets) / len(override_rets) if override_rets else 0.0
        d1, d2, d3 = st.columns(3)
        d1.metric("Trades opened against engine", n_over,
                  delta=f"{n_over/len(rows)*100:.0f}% of total")
        d2.metric("Avg net % when overriding", f"{avg_over:+.2f}%")
        d3.metric("Override-vs-engine delta",
                  f"{avg_over - sum(float(str(r['Actual net%']).rstrip('%')) for r in rows if str(r['Actual net%']).rstrip('%').lstrip('+-').replace('.', '').isdigit())/max(1,len(rows)):+.2f}pp")
        st.caption(
            "When the engine downgrades a setup (HOSTILE override, sub-65 score, "
            "etc.) and you trade anyway, the realized return on those trades is "
            "your single best honesty signal. If it's persistently negative vs "
            "your engine-aligned trades, the discipline gap is costing you."
        )
