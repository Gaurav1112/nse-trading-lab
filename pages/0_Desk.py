"""Trade Desk: TAKE / WAIT / SKIP decisions with sizing, odds and a ticket you type in yourself.

Paper mode. Nothing is ever placed. Every loader call is wrapped so a failure shows a readable
error and the rest of the page keeps working.
"""
from __future__ import annotations

import html
import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from components import theme
from nse_backtest import tape_monitor
from nse_backtest.desk import loaders, session
from nse_backtest.desk.daily import flip_conditions
from nse_backtest.desk.markets import get_profile, universe
from nse_backtest.desk.models import Action, DeskVerdict, Limits, Mode
from nse_backtest.desk.positions import review_positions

st.set_page_config(page_title="Desk | Trading Lab", page_icon="🧭", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)

DISCLAIMER = "Paper mode — nothing is ever placed. Not financial advice."
ACTION_COLOR = {"TAKE": "#00C46A", "WAIT": "#FFB800", "SKIP": "#FF3355"}
STATUS_COLOR = {"PASS": "#00C46A", "WARN": "#FFB800", "BLOCK": "#FF3355"}


# ---------------------------------------------------------------- formatting

def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _r(x) -> str:
    return "n/a" if x is None else f"{x:+.2f}R"


def _today() -> str:
    return date.today().isoformat()


def _safe(label: str, fn, *args, **kw):
    """(result, None) or (None, message); the failure is shown and the page continues."""
    try:
        return fn(*args, **kw), None
    except Exception as e:  # noqa: BLE001 - every failure must be readable, never a blank page
        msg = f"{label}: {e}" if str(e) else f"{label}: {type(e).__name__}"
        st.error(msg)
        return None, msg


def _now_hhmm(profile) -> str:
    try:
        return datetime.now(ZoneInfo(profile.tz)).strftime("%H:%M")
    except Exception:  # noqa: BLE001 - a bad tz name must not hide a result
        return datetime.now().strftime("%H:%M")


def _attempt(label: str, fn, *args, **kw) -> dict:
    """Run fn quietly; return {"ok": result, "at": HH:MM} or {"error": msg}. Rendered from session state."""
    try:
        return {"ok": fn(*args, **kw)}
    except Exception as e:  # noqa: BLE001 - every failure must be readable, never a blank page
        return {"error": f"{label}: {e}" if str(e) else f"{label}: {type(e).__name__}"}


# ---------------------------------------------------------------- verdict card

def _check_summary(v: DeskVerdict) -> str:
    """One line: 'N BLOCK · M WARN' with the names; red if any BLOCK, amber if only WARN, else green."""
    if not v.checks:
        return (f"<span style='color:{STATUS_COLOR['WARN']};font-weight:700'>"
                "No checks were run — do not trade on this verdict</span>")
    blocks, warns = [html.escape(c.name) for c in v.blocks()], [html.escape(c.name) for c in v.warns()]
    if not blocks and not warns:
        return f"<span style='color:{STATUS_COLOR['PASS']};font-weight:700'>All {len(v.checks)} checks passed</span>"
    color = STATUS_COLOR["BLOCK"] if blocks else STATUS_COLOR["WARN"]
    parts = [f"{len(blocks)} BLOCK · {len(warns)} WARN"]
    names = [f"{label}: {', '.join(n)}" for label, n in (("BLOCK", blocks), ("WARN", warns)) if n]
    return f"<span style='color:{color};font-weight:700'>{parts[0]} &nbsp;({' · '.join(names)})</span>"


def render_verdict(v: DeskVerdict, profile, key: str, at: str | None = None) -> None:
    m, p, s, o = profile.money, v.plan, v.sizing, v.odds
    color = ACTION_COLOR.get(v.action.value, "#888888")
    st.markdown(f"<div style='font-size:2.6rem;font-weight:800;color:{color};line-height:1.1'>"
                f"{v.action.value}</div>", unsafe_allow_html=True)
    st.markdown(f"**{v.headline}**")
    if at:
        st.caption(f"Checked at {at} (market time)")
    st.markdown(_check_summary(v), unsafe_allow_html=True)

    st.markdown("**Plan**")
    st.dataframe(pd.DataFrame([{
        "Entry": m(p.entry), "Stop": m(p.stop), "T1": m(p.target_1), "T2": m(p.target_2),
        "R / share": m(p.risk_per_share), "Basis": p.stop_basis, "Mode": p.mode.value}]),
        hide_index=True, width="stretch")
    st.caption(f"Size: {s.qty} shares · binding limit {s.binding_limit} · risk {m(s.risk_amount)} · "
               f"notional {m(s.notional)} · costs {m(s.costs)}. {s.note}")

    st.markdown("**Odds** (expectancy in R first)")
    if o is None:
        st.info("Odds unavailable — not enough history for a reliable estimate.")
    else:
        row1 = st.columns(4)
        row1[0].metric("MC expectancy", _r(o.mc_expectancy_r))
        row1[1].metric("P(target)", _pct(o.p_target))
        row1[2].metric("P(stop)", _pct(o.p_stop))
        row1[3].metric("P(time exit)", _pct(o.p_time))
        row2 = st.columns(4)
        row2[0].metric("History n", o.history_n)
        row2[1].metric("History win %", "n/a" if o.history_win_pct is None else f"{o.history_win_pct:.0f}%")
        row2[2].metric("History expectancy", _r(o.history_expectancy_r))
        row2[3].metric("Worst drawdown (p95)", "n/a" if o.dd_p95_r is None else f"{o.dd_p95_r:.1f}R")
        row3 = st.columns(2)
        row3[0].metric("P(5 losses in a row)",
                       "n/a" if o.p_5_loss_streak is None else f"{o.p_5_loss_streak * 100:.1f}%")
        noisy = o.noise_expectancy_min is not None and o.noise_expectancy_max is not None
        row3[1].metric("Noise range (shuffled data)",
                       f"{_r(o.noise_expectancy_min)} to {_r(o.noise_expectancy_max)}" if noisy else "n/a")
        st.caption(f"{o.replays} replays, seed {o.seed}.")
        if o.history_n > 0 and o.history_rs:
            try:
                import plotly.express as px
                fig = px.histogram(x=list(o.history_rs), nbins=20,
                                   labels={"x": "R multiple of past similar setups"})
                fig.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
                st.plotly_chart(fig, width="stretch", key=f"hist_{key}")
            except Exception as e:  # noqa: BLE001
                st.warning(f"Histogram unavailable: {e}")

    st.markdown("**Checks**")
    checks = pd.DataFrame([{"Status": c.status.value, "Check": c.name, "Reason": c.reason} for c in v.checks])
    if checks.empty:
        st.info("No checks were run.")
    else:
        st.dataframe(checks.style.map(
            lambda x: f"color:{STATUS_COLOR.get(x, 'inherit')};font-weight:700", subset=["Status"]),
            hide_index=True, width="stretch")

    if v.ticket:
        st.markdown("**Ticket** (type it in yourself — the desk places nothing)")
        st.code("\n".join(v.ticket), language="text")
    st.caption(DISCLAIMER)


# ---------------------------------------------------------------- sidebar

st.markdown("# 🧭 Trade Desk")
with st.sidebar:
    market = st.radio("Market", ["NSE", "US"], key="desk_market", horizontal=True)
    profile = get_profile(market)
    st.caption(DISCLAIMER)
    limits, fell_back = loaders.default_limits(profile), False
    loaded, _ = _safe("Could not read settings", session.limits_with_status, profile)
    if loaded is not None:
        limits, fell_back = loaded
    if fell_back:
        st.warning("Settings file unreadable or invalid — using defaults. Saving will back it up and replace it.")
    with st.expander("Settings", expanded=fell_back):
        k = f"desk_set_{market}_"
        cap = st.number_input(f"Capital ({profile.currency})", min_value=0.0, value=float(limits.capital),
                              step=1000.0, key=k + "cap")
        risk = st.number_input("Risk per trade %", min_value=0.0, max_value=100.0,
                               value=float(limits.risk_per_trade_pct), step=0.25, key=k + "risk")
        maxpos = st.number_input("Max position %", min_value=0.0, max_value=100.0,
                                 value=float(limits.max_position_pct), step=1.0, key=k + "maxpos")
        loss = st.number_input("Daily loss limit %", min_value=0.0, max_value=100.0,
                               value=float(limits.daily_loss_limit_pct), step=0.25, key=k + "loss")
        pdt = limits.pdt_guard
        if market == "US":
            pdt = st.checkbox("PDT guard", value=limits.pdt_guard, key=k + "pdt")
        if st.button("Save settings", key=k + "save"):
            try:
                from dataclasses import replace
                loaders.save_limits(profile, replace(limits, capital=cap, risk_per_trade_pct=risk,
                                                     max_position_pct=maxpos, daily_loss_limit_pct=loss,
                                                     pdt_guard=bool(pdt)))
                st.success("Saved.")
                limits, fell_back = session.limits_with_status(profile)
            except Exception as e:  # noqa: BLE001 - validation errors are shown, nothing is saved
                st.error(f"Not saved: {e}")


# ---------------------------------------------------------------- header: regime

def _regime_state(profile):
    """(regime_df, tape, error). Only a successful load is cached (per market and day); a failure retries."""
    key = ("regime", profile.code, _today())
    if key in st.session_state:
        return st.session_state[key]
    try:
        df = loaders.load_regime(profile)
        tape = tape_monitor.assess_tape(df)
    except Exception as e:  # noqa: BLE001
        return None, None, str(e) or type(e).__name__
    st.session_state[key] = (df, tape, None)
    return st.session_state[key]


regime_df, tape, regime_err = _regime_state(profile)
if tape is None:
    st.warning("Regime unknown — treat as HOSTILE" + (f" ({regime_err})" if regime_err else ""))
else:
    st.markdown(f"<span style='background:{tape.color};color:#000;padding:4px 12px;border-radius:6px;"
                f"font-weight:700'>{tape.regime}</span> &nbsp; {tape.recommendation}", unsafe_allow_html=True)
try:
    flips = flip_conditions(regime_df, profile)
except Exception as e:  # noqa: BLE001
    flips = (f"Flip conditions unavailable: {e}",)
st.markdown("**What would change it**")
st.markdown("\n".join(f"- {c}" for c in flips) if flips else "- Regime is already TRENDING")
regime_name = tape.regime if tape else None

tab_plan, tab_check, tab_pos, tab_intra = st.tabs(["Today's Plan", "Check a Trade", "My Positions", "Intraday"])


# ---------------------------------------------------------------- Today's Plan

with tab_plan:
    plan_key = ("plan", market, _today())
    if st.button("Build today's plan", key="desk_build_plan"):
        with st.spinner("Scanning the universe — this can take a few minutes the first time..."):
            state = _attempt("Could not build the plan", session.run_daily_plan, profile, limits, universe(profile))
        if "ok" in state:
            state["at"] = _now_hhmm(profile)
        st.session_state[plan_key] = state  # a failure REPLACES any earlier success
    pstate = st.session_state.get(plan_key)
    plan = pstate.get("ok") if pstate else None
    if pstate and "error" in pstate:
        st.error(pstate["error"])
    elif plan is None:
        st.info("Press the button to build today's plan. No trade is a valid answer.")
    else:
        st.caption(f"Built at {pstate['at']} (market time)")
        st.subheader(f"TAKE ({len(plan.take)})")
        if not plan.take:
            st.info("Nothing qualifies today — no trade is a valid answer.")
        for i, v in enumerate(plan.take):
            with st.container(border=True):
                render_verdict(v, profile, f"plan_{i}", at=pstate["at"])
        st.subheader(f"WATCH ({len(plan.watch)})")
        if plan.watch:
            rows = []
            for v in plan.watch:
                trig = v.trigger or v.plan.entry
                px_now = v.current_price
                ok = isinstance(px_now, (int, float)) and math.isfinite(px_now) and px_now > 0
                rows.append({"Symbol": v.plan.symbol, "Trigger": profile.money(trig),
                             "Distance %": round((trig / px_now - 1) * 100, 2) if ok else None,
                             "Blocked by": ", ".join(c.name for c in v.blocks()) or "waiting for trigger"})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                         column_config={"Distance %": st.column_config.NumberColumn(format="%.1f%%")})
        else:
            st.caption("Nothing on the watch list.")
        with st.expander(f"Skipped ({len(plan.skipped)}) and unavailable ({len(plan.unavailable)})"):
            if plan.skipped:
                st.dataframe(pd.DataFrame(plan.skipped, columns=["Symbol", "Why"]), hide_index=True, width="stretch")
            if plan.unavailable:
                st.write("No data: " + ", ".join(plan.unavailable))
        st.caption(DISCLAIMER)


# ---------------------------------------------------------------- Check a Trade

with tab_check:
    c1, c2 = st.columns([2, 1])
    symbol = c1.text_input("Symbol", key="desk_symbol", placeholder="e.g. RELIANCE" if market == "NSE" else "e.g. AAPL")
    mode = c2.radio("Mode", ["swing", "intraday"], key="desk_mode", horizontal=True)
    e1, e2, e3 = st.columns(3)
    entry = e1.number_input("Entry (0 = auto)", min_value=0.0, value=0.0, key="desk_entry")
    stop = e2.number_input("Stop (0 = auto)", min_value=0.0, value=0.0, key="desk_stop")
    target = e3.number_input("Target (0 = auto)", min_value=0.0, value=0.0, key="desk_target")
    result_key = ("check", market, symbol.strip().upper(), mode, entry, stop, target)
    if st.button("Check this trade", key="desk_check"):
        with st.spinner("Checking..."):
            try:
                v = session.run_check(profile, symbol, Mode(mode), limits, entry=entry or None,
                                      stop=stop or None, target=target or None, regime=regime_name)
                cstate = {"ok": v, "at": _now_hhmm(profile)}
            except ValueError as e:
                cstate = {"error": str(e) or "Invalid input"}
            except Exception as e:  # noqa: BLE001
                cstate = {"error": f"Check failed: {type(e).__name__}: {e}"}
        st.session_state[result_key] = cstate  # a failure REPLACES any earlier success
    cstate = st.session_state.get(result_key)
    if cstate is not None:
        if "error" in cstate:
            st.error(cstate["error"])
        else:
            with st.container(border=True):
                render_verdict(cstate["ok"], profile, "check", at=cstate["at"])


# ---------------------------------------------------------------- My Positions

with tab_pos:
    pos_key = ("positions", market)
    loaded_now = False
    if st.button("Refresh positions", key="desk_refresh_pos") or pos_key not in st.session_state:
        loaded_now = True
        def _review():
            positions = loaders.market_positions(profile)
            syms = sorted({str(p["symbol"]) for p in positions if p.get("symbol") and not p.get("closed")})
            frames = session.load_frames(profile, syms)
            return review_positions(positions, {k: f for k, f in frames.items() if f is not None}, profile)
        actions, err = _safe("Could not review positions", _review)
        # never cache a failure as an empty result: keep the message and re-show it until a Refresh succeeds
        st.session_state[pos_key] = {"error": err} if err is not None else {"ok": actions}
    state = st.session_state[pos_key]
    actions = state.get("ok")
    if "error" in state:
        if not loaded_now:  # a fresh load already showed it via _safe
            st.error(state["error"])
    elif not actions:
        st.info(f"No open {market} positions.")
    else:
        df = pd.DataFrame([{
            "Symbol": a.symbol, "Action": a.action,
            "Now": "n/a" if a.current_price is None else profile.money(a.current_price),
            "Entry": profile.money(a.entry), "Stop": profile.money(a.stop),
            "Suggested stop": "" if a.suggested_stop is None else profile.money(a.suggested_stop),
            "R so far": _r(a.r_so_far), "Reason": a.reason} for a in actions])

        def _hl(row):
            bg = {"EXIT": "background-color:#5a1620", "TIGHTEN_STOP": "background-color:#5a4410"}.get(row["Action"], "")
            return [bg] * len(row)
        st.dataframe(df.style.apply(_hl, axis=1), hide_index=True, width="stretch")
    st.caption(DISCLAIMER)


# ---------------------------------------------------------------- Intraday

with tab_intra:
    uni = universe(profile)
    picked = st.multiselect("Symbols", uni, default=uni[:15], key=f"desk_intra_syms_{market}")
    scan_key = ("intraday", market, _today())
    if st.button("Scan", key="desk_scan"):
        if not picked:
            st.error("Pick at least one symbol.")
        else:
            with st.spinner("Scanning 15-minute bars..."):
                state = _attempt("Intraday scan failed", session.run_intraday, profile, limits, picked,
                                 regime=regime_name)
            if "ok" in state:
                state["at"] = _now_hhmm(profile)
            st.session_state[scan_key] = state  # a failure REPLACES any earlier success
    istate = st.session_state.get(scan_key)
    if istate is not None and "error" in istate:
        st.error(istate["error"])
    elif istate is not None:
        verdicts, failures = istate["ok"]
        st.caption(f"Built at {istate['at']} (market time)")
        if not verdicts:
            st.info("No setups scanned.")
        else:
            st.dataframe(pd.DataFrame([{
                "Symbol": v.plan.symbol, "Action": v.action.value, "Headline": v.headline,
                "MC R": _r(v.odds.mc_expectancy_r if v.odds is not None else None)} for v in verdicts]),
                hide_index=True, width="stretch")
            for i, v in enumerate(verdicts):
                if v.action is Action.SKIP:
                    continue
                with st.expander(f"{v.action.value} — {v.plan.symbol}", expanded=v.action is Action.TAKE):
                    render_verdict(v, profile, f"intra_{i}", at=istate["at"])
        if failures:
            with st.expander(f"Not scanned ({len(failures)})"):
                for s, why in failures:
                    st.write(f"{s}: {why}")
    st.caption(DISCLAIMER)
