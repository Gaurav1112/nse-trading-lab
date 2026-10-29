from __future__ import annotations
from datetime import datetime, timedelta, timezone
import streamlit as st


def classify(health: dict, now: datetime) -> tuple[str, str]:
    """Classify pipeline health as healthy/degraded/dead.

    Args:
        health: Dict with keys 'last_run_ts' (ISO string), 'errors' (list)
        now: Current datetime for age calculation

    Returns:
        Tuple of (state, message) where state ∈ {"healthy", "degraded", "dead"}
    """
    ts_str = health.get("last_run_ts")
    if ts_str is None:
        return "dead", "Pipeline has never run"
    last = datetime.fromisoformat(ts_str)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = now - last
    if age > timedelta(minutes=30):
        return "dead", f"Last run {int(age.total_seconds()/60)} min ago — DO NOT TRADE off cached signals"
    if age > timedelta(minutes=10) or health.get("errors"):
        errs = health.get("errors", [])
        why = f" ({errs[0]})" if errs else ""
        return "degraded", f"Last run {int(age.total_seconds()/60)} min ago{why}"
    return "healthy", f"Last run {int(age.total_seconds()/60)} min ago"


def render_badge(health: dict, now: datetime, source_label: str = "yfinance") -> None:
    """Render trust badge to Streamlit.

    Args:
        health: Health dict with 'last_run_ts' and 'errors'
        now: Current datetime for age calculation
        source_label: Price source label (e.g., 'yfinance', 'fyers')
    """
    state, msg = classify(health, now)
    color = {"healthy": "#00FF87", "degraded": "#FFB800", "dead": "#FF4D4D"}[state]
    dot = {"healthy": "🟢", "degraded": "🟡", "dead": "🔴"}[state]
    prices_line = (
        f"Live prices ON ({source_label})" if source_label == "fyers"
        else f"⚠ Live prices degraded ({source_label} — showing 15-min lag)"
    )
    st.markdown(
        f'<div style="border:2px solid {color};border-radius:10px;padding:8px 14px;background:#0D1526;'
        f'margin:0 0 12px 0;font-size:13px;color:#C9D5E0">'
        f'<b>{dot} Pipeline {state}</b> · {msg} · {prices_line}'
        f'</div>',
        unsafe_allow_html=True,
    )
