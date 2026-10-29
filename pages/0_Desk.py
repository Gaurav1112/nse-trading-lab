"""Trade Desk: TAKE / WAIT / SKIP decisions with sizing, odds and a ticket you type in yourself.

Paper mode. Nothing is ever placed. Every loader call is wrapped so a failure shows a readable
error and the rest of the page keeps working.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
from dataclasses import asdict, replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from components import desk_card, theme
from nse_backtest.desk import loaders, session, strength
from nse_backtest.desk.charts import projected_vs_real_chart
from nse_backtest.desk.projections import projected_vs_real, resolved_without_r
from nse_backtest.desk.scoreboard import RECORD_LABELS
from nse_backtest.desk.scoreboard_io import SCOREBOARD_COVERAGE, SCOREBOARD_DISCLAIMER
from nse_backtest.desk.daily import flip_conditions, plan_errors_line
from nse_backtest.desk.markets import get_profile, universe
from nse_backtest.desk.models import Action, DeskVerdict, Mode, Side
from nse_backtest.desk.shortcheck import edge_line
from nse_backtest.desk.positions import review_positions
from nse_backtest.desk.textsafe import shown
from nse_backtest.desk.tradecard import build_card
from nse_backtest.desk.verdict import is_quick

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


def _market_now(profile) -> datetime:
    try:
        return datetime.now(ZoneInfo(profile.tz))
    except Exception:  # noqa: BLE001 - a bad tz name must not hide a result
        return datetime.now()


def _today(profile) -> str:
    """Market-today (profile tz), so caches roll over at the market's midnight, not the laptop's."""
    return _market_now(profile).date().isoformat()


def _limits_key(limits) -> str:
    return hashlib.sha256(json.dumps(asdict(limits), sort_keys=True, default=str).encode()).hexdigest()[:12]


def _safe(label: str, fn, *args, **kw):
    """(result, None) or (None, message); the failure is shown and the page continues."""
    try:
        return fn(*args, **kw), None
    except Exception as e:  # noqa: BLE001 - every failure must be readable, never a blank page
        msg = f"{label}: {shown(e)}" if str(e) else f"{label}: {type(e).__name__}"
        st.error(msg)
        return None, msg


def _now_hhmm(profile) -> str:
    """Market date and time, e.g. '2026-10-29 10:05'."""
    return _market_now(profile).strftime("%Y-%m-%d %H:%M")


def _attempt(label: str, fn, *args, **kw) -> dict:
    """Run fn quietly; return {"ok": result, "at": HH:MM} or {"error": msg}. Rendered from session state."""
    try:
        return {"ok": fn(*args, **kw)}
    except Exception as e:  # noqa: BLE001 - every failure must be readable, never a blank page
        return {"error": f"{label}: {shown(e)}" if str(e) else f"{label}: {type(e).__name__}"}


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


def _mode_label(p) -> str:
    """The plan's mode, with the SHORT badge for an intraday short."""
    return f"{p.mode.value} SHORT" if p.is_short else p.mode.value


def render_verdict(v: DeskVerdict, profile, key: str, at: str | None = None, *, show_ticket: bool = True) -> None:
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
        "R / share": m(p.risk_per_share), "Basis": p.stop_basis, "Mode": _mode_label(p)}]),
        hide_index=True, width="stretch")
    st.caption(f"Size: {s.qty} shares · binding limit {s.binding_limit} · risk {m(s.risk_amount)} · "
               f"notional {m(s.notional)} · costs {m(s.costs)}. {s.note}")

    st.markdown("**Odds** (expectancy in R first)" + (" — a short: the stock's own HOSTILE days, 2× slippage in the "
                                                      "history; the pooled short_edge check decides TAKE"
                                                      if p.is_short else ""))
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
        row3[1].metric("Candle-noise expectancy range",
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
                st.warning(f"Histogram unavailable: {shown(e)}")

    st.markdown("**Checks**")
    checks = pd.DataFrame([{"Status": c.status.value, "Check": c.name, "Reason": c.reason} for c in v.checks])
    if checks.empty:
        st.info("No checks were run.")
    else:
        st.dataframe(checks.style.map(
            lambda x: f"color:{STATUS_COLOR.get(x, 'inherit')};font-weight:700", subset=["Status"]),
            hide_index=True, width="stretch")

    if show_ticket and v.ticket:
        st.markdown("**Ticket** (type it in yourself — the desk places nothing)")
        st.code("\n".join(v.ticket), language="text")
    st.caption(DISCLAIMER)


def _render_card(v: DeskVerdict, profile, limits, key: str, *, header: bool = True) -> None:
    """The trade card (components/desk_card.py): money, levels, your-call ticket, charts, projections, research."""
    desk_card.render_card(v, profile, limits, key, today=_today(profile), header=header,
                          flips=tuple(flips))  # the header's flip conditions, under a "Market says sit out" banner


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
                st.error(f"Not saved: {shown(e)}")


# ---------------------------------------------------------------- header: regime

def _regime_state(profile):
    """(regime_df, tape, error). Cached per market and day only when the tape was classified;
    a failed, stale or unclassifiable regime is retried on the next run."""
    key = ("regime", profile.code, _today(profile))
    if key in st.session_state:
        return st.session_state[key]
    warns: list[str] = []
    try:
        df, tape = session.resolve_tape(profile, warns.append)
    except Exception as e:  # noqa: BLE001
        return None, None, str(e) or type(e).__name__
    if tape is None:
        return df, None, "; ".join(warns) or None
    st.session_state[key] = (df, tape, None)
    return st.session_state[key]


regime_df, tape, regime_err = _regime_state(profile)
if tape is None:
    st.warning("Regime unknown — treat as HOSTILE" + (f" ({shown(regime_err)})" if regime_err else ""))
else:
    st.markdown(f"<span style='background:{tape.color};color:#000;padding:4px 12px;border-radius:6px;"
                f"font-weight:700'>{tape.regime}</span> &nbsp; {tape.recommendation}", unsafe_allow_html=True)
try:
    flips = flip_conditions(regime_df, profile)
except Exception as e:  # noqa: BLE001
    flips = (f"Flip conditions unavailable: {shown(e)}",)
st.markdown("**What would change it**")
st.markdown("\n".join(f"- {c}" for c in flips) if flips else "- Regime is already TRENDING")
regime_name = tape.regime if tape else None

tab_choose, tab_plan, tab_check, tab_pos, tab_intra, tab_score = st.tabs(
    ["Choose a Trade", "Today's Plan", "Check a Trade", "My Positions", "Intraday", "Scoreboard"])
# Today's Plan and the Intraday scan are cached under these keys; Choose a Trade reuses them.
PLAN_KEY = ("plan", market, _today(profile), _limits_key(limits))
SCAN_KEY = ("intraday", market, _today(profile), _limits_key(limits))


# ---------------------------------------------------------------- Today's Plan

def _positions_line(market: str) -> str:
    """'N positions need action' from the My Positions review when it is loaded; otherwise a pointer."""
    state = st.session_state.get(("positions", market))
    if not state or "error" in state:
        return "Open My Positions to review" + (" — the last review failed" if state else "")
    n = sum(1 for a in (state.get("ok") or []) if a.action != "HOLD")
    return f"{n} position{'' if n == 1 else 's'} need{'s' if n == 1 else ''} action — see My Positions"


def _plan_errors(plan_now) -> None:
    """A code bug on some symbols is red, never an empty list that looks like a quiet day."""
    line = plan_errors_line(plan_now)
    if line:
        st.error(shown(line))


with tab_plan:
    st.markdown(f"**{_positions_line(market)}**")
    plan_key = PLAN_KEY
    if st.button("Build today's plan", key="desk_build_plan"):
        with st.spinner("Scanning the universe — this can take a few minutes the first time..."):
            state = _attempt("Could not build the plan", session.run_daily_plan, profile, limits, universe(profile),
                             regime=regime_name, tape=tape)  # the header's regime: header and plan agree
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
        _plan_errors(plan)
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
        if plan.others:
            with st.expander(f"Also checked, not on today's lists ({len(plan.others)}) — ranked with the money "
                             "on Choose a Trade"):
                st.dataframe(pd.DataFrame([(v.plan.symbol, v.action.value, v.headline) for v in plan.others],
                                          columns=["Symbol", "Desk says", "Why"]), hide_index=True, width="stretch")
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
    side = "long"
    if market == "NSE":  # shorts are NSE only (F&O stocks, MIS)
        side = c2.radio("Side", ["long", "short"], key="desk_side", horizontal=True)
    if side == "long":
        mode = c2.radio("Mode", ["swing", "intraday"], key="desk_mode", horizontal=True)
    else:  # no Mode choice: a short is intraday
        mode = "intraday"
        st.caption("Shorts are intraday (MIS) only: an opening-range breakdown short on an F&O stock, squared off "
                   "by 15:10 IST. Same checks as the scan; at most WAIT until the pooled short history proves "
                   "the edge, and a TAKE also needs the stock's own simulated short expectancy above zero.")
    e1, e2, e3 = st.columns(3)
    entry = e1.number_input("Entry (0 = auto)", min_value=0.0, value=0.0, key="desk_entry",
                            help="0 = the desk's own entry (breakout above the 20-day high for swing), the same entry "
                                 "as today's plan; the close when the stock is already at that high. Intraday: the "
                                 "opening-range high.")
    stop = e2.number_input("Stop (0 = auto)", min_value=0.0, value=0.0, key="desk_stop")
    target = e3.number_input("Target (0 = auto)", min_value=0.0, value=0.0, key="desk_target")
    result_key = ("check", market, _today(profile), _limits_key(limits), symbol.strip().upper(), mode,
                  entry, stop, target, side)
    if st.button("Check this trade", key="desk_check"):
        with st.spinner("Checking..."):
            try:
                v = session.run_check(profile, symbol, Mode(mode), limits, entry=entry or None,
                                      stop=stop or None, target=target or None, regime=regime_name,
                                      desk_entry=True, side=Side(side))
                cstate = {"ok": v, "at": _now_hhmm(profile)}
            except ValueError as e:
                cstate = {"error": shown(e) if str(e) else "Invalid input"}
            except Exception as e:  # noqa: BLE001
                cstate = {"error": f"Check failed: {type(e).__name__}: {shown(e)}"}
        st.session_state[result_key] = cstate  # a failure REPLACES any earlier success
    cstate = st.session_state.get(result_key)
    if cstate is not None:
        if "error" in cstate:
            st.error(cstate["error"])
        else:
            with st.container(border=True):
                render_verdict(cstate["ok"], profile, "check", at=cstate["at"], show_ticket=False)
                st.markdown("---")
                _render_card(cstate["ok"], profile, limits, "check", header=False)


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
            "Entry": "n/a" if a.entry is None else profile.money(a.entry),
            "Stop": "n/a" if a.stop is None else profile.money(a.stop),
            "Suggested stop": "" if a.suggested_stop is None else profile.money(a.suggested_stop),
            "R so far": _r(a.r_so_far), "Reason": a.reason} for a in actions])

        def _hl(row):
            bg = {"EXIT": "background-color:#5a1620", "TIGHTEN_STOP": "background-color:#5a4410"}.get(row["Action"], "")
            return [bg] * len(row)
        st.dataframe(df.style.apply(_hl, axis=1), hide_index=True, width="stretch")
    st.caption(DISCLAIMER)


# ---------------------------------------------------------------- Intraday

def _show_edge(edge, *, symbols=None, cached=False) -> None:
    syms = "" if symbols is None else f" over {symbols} eligible symbol{'' if symbols == 1 else 's'}"
    when = "cached for today" if cached else "computed now, cached for today"
    line = f"Short edge{syms}: {edge_line(edge)} ({when})."
    if edge.proven:
        st.success(line + " Shorts can be a desk TAKE when every other check passes.")
    else:
        st.warning(line + " Every short stays at most WAIT (still your call to override).")


def _short_edge_panel(profile) -> None:
    """Today's pooled ORB-short figure (read from the daily cache, no fetch), and a button that computes it."""
    key = ("shortcheck", profile.code, _today(profile))
    c1, c2 = st.columns([1, 1])
    run = c1.button("Check the short edge (pooled history)", key="desk_shortcheck",
                    help="Replays the desk's ORB short on every HOSTILE day of the F&O universe's 15-minute bars at "
                         "2× slippage. Fetches bars once; the figure is cached for the day.")
    redo = c2.button("Recompute", key="desk_shortcheck_refresh")
    if run or redo:
        bar = st.progress(0.0, text="Loading 15-minute bars...")

        def _progress(done, total, sym):
            bar.progress(min(1.0, (done - 1) / max(total, 1)), text=f"Replaying {sym} ({done} of {total})...")
        try:
            res = session.run_shortcheck(profile, refresh=bool(redo), on_progress=_progress)
            st.session_state[key] = {"ok": res}
        except Exception as e:  # noqa: BLE001 - never a blank tab; the gate stays closed
            st.session_state[key] = {"error": f"Short edge check failed: {shown(e)}"}
        bar.empty()
    state = st.session_state.get(key)
    if state is not None and "error" in state:
        st.error(state["error"])
    elif state is not None:
        res = state["ok"]
        _show_edge(res.edge, symbols=res.symbols, cached=res.cached)
    else:
        edge, why = session.short_edge_today(profile)
        if edge is None:
            st.info(f"Short edge not computed today ({shown(why)}) — every short stays at most WAIT until it is.")
        else:
            _show_edge(edge, cached=True)


with tab_intra:
    if session.shorts_today(profile, regime_name):
        st.caption("HOSTILE NSE day: the scan also checks each symbol as an opening-range breakdown SHORT (MIS, F&O "
                   "stocks only, squared off by 15:10 IST). Shorts stay at most WAIT until the desk has proven the "
                   "edge on its own history.")
    if market == "NSE":
        _short_edge_panel(profile)
    uni = universe(profile)
    picked = st.multiselect("Symbols", uni, default=uni, key=f"desk_intra_syms_{market}")
    scan_key = SCAN_KEY
    if st.button("Scan", key="desk_scan"):
        if not picked:
            st.error("Pick at least one symbol.")
        else:
            bar = st.progress(0.0, text=f"Scanning 15-minute bars (0 of {len(picked)})...")

            def _progress(done, total, sym):
                bar.progress(min(1.0, (done - 1) / max(total, 1)), text=f"Scanning {sym} ({done} of {total})...")
            state = _attempt("Intraday scan failed", session.run_intraday, profile, limits, picked,
                             regime=regime_name, on_progress=_progress)
            bar.empty()
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
            now_m = desk_card.market_now(profile)
            rows = []  # (verdict now: a Re-check replaces a stale one, as displayed, stale reason)
            for v in verdicts:
                cur = desk_card.current_verdict(v, "intra")
                stale = desk_card.stale_reason(cur, profile, now_m)
                shown_v = (replace(cur, headline=f"{cur.action.value} {cur.plan.symbol}"
                                   f"{' SHORT' if cur.plan.is_short else ''} (stale — re-check)")
                           if stale is not None else cur)
                rows.append((cur, shown_v, stale))
            st.dataframe(pd.DataFrame([{
                "Symbol": sv.plan.symbol, "Side": "SHORT" if sv.plan.is_short else "long",
                "Action": sv.action.value, "Headline": sv.headline,
                "MC R": _r(sv.odds.mc_expectancy_r if sv.odds is not None else None)} for _, sv, _ in rows]),
                hide_index=True, width="stretch")
            for i, (cur, shown_v, stale) in enumerate(rows):
                ok = f"desk_intra_open_{cur.input_hash[:12]}"
                with st.expander(f"{cur.action.value} — {cur.plan.symbol}" + (" SHORT" if cur.plan.is_short else ""),
                                 expanded=cur.action is Action.TAKE):
                    if st.button("Close card" if st.session_state.get(ok) else "Open card", key=f"{ok}_btn"):
                        st.session_state[ok] = not st.session_state.get(ok)
                        st.rerun()
                    if st.session_state.get(ok):  # every gate (stale, data-broken, override) lives in the card
                        _render_card(cur, profile, limits, "intra")
                    elif cur.action is Action.SKIP:
                        st.caption(shown(cur.headline))
                    else:
                        render_verdict(shown_v, profile, f"intra_{i}", at=istate["at"], show_ticket=stale is None)
                        if stale is not None:  # never a stale ticket or headline: re-check first
                            desk_card.recheck_prompt(cur, profile, limits, "intra",
                                                     f"intra_{i}_{cur.input_hash[:12]}", stale)
        if failures:
            with st.expander(f"Not scanned ({len(failures)})"):
                for s, why in failures:
                    st.write(f"{s}: {shown(why)}")
    st.caption(DISCLAIMER)


# ---------------------------------------------------------------- Scoreboard

def _score_mode(mode, side) -> str:
    """"intraday", "swing", or "intraday short": short stats are never pooled with longs."""
    return f"{mode.value} short" if getattr(side, "value", side) == "short" else mode.value


def _score_banner(s) -> None:
    text = f"**{s.market} {_score_mode(s.mode, s.side)}** — {s.verdict}"
    if s.verdict.startswith(("Losing", "Worse")):
        st.error(text)
    elif s.verdict.startswith("Matching"):
        st.success(text)
    else:
        st.info(text)


def _render_projected_vs_real(outcomes, s) -> None:
    """Projected vs realised R for one (record, market, mode, side) group, or an info line without any projection."""
    group = [o for o in outcomes if (o.candidate.record, o.candidate.market, o.candidate.mode, o.candidate.side) ==
             (s.record, s.market, s.mode, s.side)]
    cum_proj, cum_real, pairs, n_without = projected_vs_real(group)
    n_no_r = resolved_without_r(group)
    st.markdown(f"**Projected vs realised R — {_score_mode(s.mode, s.side)}**")
    if n_no_r:  # never dropped silently
        st.caption(f"{n_no_r} resolved trade{'s' if n_no_r != 1 else ''} without a realised R — left out of this "
                   "comparison.")
    if not pairs:
        st.info("No projections to compare yet")
        return
    st.plotly_chart(projected_vs_real_chart(cum_proj, cum_real, pairs, n_without), width="stretch",
                    key=f"pvr_{s.record}_{s.market}_{s.mode.value}" + ("_short" if s.side.value == "short" else ""))
    other = sum(o.realised_r for o in group if o.candidate.mc_expectancy_r is None and o.realised_r is not None)
    st.caption(f"{n_without} resolved trade{'s' if n_without != 1 else ''} without a projection"
               + (f" (realised {other:+.2f}R, not on the chart)." if n_without else "."))


def _render_record(stats, outcomes=()) -> None:
    """One record's verdict banners first, then its numbers and charts (records are never pooled)."""
    for s in stats:
        _score_banner(s)
    if stats:
        st.dataframe(pd.DataFrame([{
            "Mode": _score_mode(s.mode, s.side), "Trades": s.n, "Win %": None if s.win_pct is None else round(s.win_pct, 1),
            "Mean realised": _r(s.mean_r), "Predicted MC": _r(s.mean_pred_mc_r),
            "Predicted history": _r(s.mean_pred_hist_r),
            "95% CI": "n/a" if s.ci_low is None else f"{_r(s.ci_low)} to {_r(s.ci_high)}",
            "Max drawdown": "n/a" if s.max_dd_r is None else f"{s.max_dd_r:.2f}R",
            "Not filled": s.not_filled, "Open": s.open, "Unavailable": s.unavailable,
            "Prediction coverage": _pct(s.pred_coverage)} for s in stats]), hide_index=True, width="stretch")
        for s in stats:
            if s.cumulative_r:
                st.markdown(f"**Cumulative R — {_score_mode(s.mode, s.side)}**")
                st.line_chart(pd.DataFrame({"cumulative R": list(s.cumulative_r)}))
            _render_projected_vs_real(outcomes, s)


NONE_YET = {"desk": "No desk picks logged yet.", "override": "No overrides logged yet.",
            "sat_out": "No sat-out trades logged yet — the desk logs one when only the market regime blocks a TAKE "
                       "in Today's Plan."}


def _render_scoreboard(res, profile, warnings=()) -> None:
    for w in warnings:  # bar-load failures, cache problems: the trades they hit show as data_unavailable
        st.warning(shown(w))
    if res.rejects:
        k = len(res.rejects)
        st.warning(f"{k} audit line{'s' if k != 1 else ''} could not be read")
    if getattr(res, "note", None):
        st.info(res.note)
    if not res.outcomes:
        st.info(f"No desk picks or overrides logged yet for {profile.code} — run plan, check or the intraday scan, "
                "or choose a trade")
    else:
        for record, label in RECORD_LABELS.items():
            st.subheader(label)
            stats = [s for s in res.stats if s.record == record]
            if not stats:
                st.caption(NONE_YET.get(record, f"No {record} trades logged yet."))
            _render_record(stats, res.outcomes)
    if res.outcomes:
        st.dataframe(pd.DataFrame([{
            "Date": o.candidate.created_at.astimezone(ZoneInfo(profile.tz)).date().isoformat(),
            "Record": RECORD_LABELS.get(o.candidate.record, o.candidate.record),
            "Symbol": o.candidate.symbol, "Mode": _score_mode(o.candidate.mode, o.candidate.side),
            "Status": o.status.value,
            "Fill": "" if o.fill_price is None else profile.money(o.fill_price),
            "Exit": "" if o.exit_price is None else profile.money(o.exit_price),
            "Realised R": _r(o.realised_r) if o.realised_r is not None else (
                f"({_r(o.mtm_r)} open)" if o.mtm_r is not None else ""),
            "Predicted MC R": _r(o.candidate.mc_expectancy_r), "Note": o.note} for o in res.outcomes]),
            hide_index=True, width="stretch")
    with st.expander(f"Rejected audit lines ({len(res.rejects)})" + (" · cache note" if res.cache_note else "")):
        for ln, why in res.rejects:
            st.write(f"line {ln}: {shown(why)}")
        if res.cache_note:
            st.warning(shown(res.cache_note))
        if not res.rejects and not res.cache_note:
            st.caption("None.")


with tab_score:
    st.caption(f"{SCOREBOARD_DISCLAIMER} {SCOREBOARD_COVERAGE}")
    score_key = ("scoreboard", market)
    if st.button("Load scoreboard", key="desk_load_scoreboard"):
        score_warnings: list[str] = []
        with st.spinner("Replaying logged TAKEs — resolved trades come from the cache..."):
            sstate = _attempt("Could not build the scoreboard", session.run_scoreboard, market,
                              on_warning=score_warnings.append)
        if "ok" in sstate:
            sstate["at"] = _now_hhmm(profile)
            sstate["warnings"] = list(score_warnings)
        st.session_state[score_key] = sstate  # a failure REPLACES any earlier success
    sstate = st.session_state.get(score_key)
    if sstate is None:
        st.info("Press the button to load the scoreboard (it may fetch price data).")
    elif "error" in sstate:
        st.error(sstate["error"])
    else:
        st.caption(f"Loaded at {sstate['at']} (market time)")
        try:
            _render_scoreboard(sstate["ok"], profile, sstate.get("warnings", ()))
        except Exception as e:  # noqa: BLE001 - never a blank tab
            st.error(f"Could not show the scoreboard: {type(e).__name__}: {shown(e)}")
    st.caption(DISCLAIMER)



# ---------------------------------------------------------------- Choose a Trade
# Rendered last (tabs can be filled in any order) so it sees a plan or scan built on its own tab in this run.

def _dash(x) -> str:
    return "—" if x is None else profile.money(x)


def _to_entry_pct(v: DeskVerdict) -> float | None:
    px = v.current_price
    ok = isinstance(px, (int, float)) and not isinstance(px, bool) and math.isfinite(px) and px > 0
    return round((v.plan.entry / px - 1) * 100, 2) if ok else None


def _candidate_row(v: DeskVerdict) -> dict:
    p = v.plan
    try:
        card = build_card(v, profile, limits)
    except Exception:  # noqa: BLE001 - bad geometry: the card itself says why
        card = None
    blocks = ", ".join(c.name for c in v.blocks())
    built = desk_card.built_at(v, profile)
    e = session.expectancy(v)
    quick = is_quick(v)
    why = (blocks or v.headline) if v.action is not Action.TAKE else (
        "not in today's top picks — run a Full check before choosing" if quick else "")
    return {"Symbol": p.symbol, "Mode": _mode_label(p), "Desk says": v.action.value,
            "Checked": "quick (last close)" if quick else "full",
            "Built": "—" if built is None else f"{built:%H:%M}",
            "Entry": profile.money(p.entry), "To entry %": _to_entry_pct(v), "Stop": profile.money(p.stop),
            "T1": profile.money(p.target_1),
            "T2": profile.money(p.target_2), "Qty": str(card.qty if card else v.sizing.qty),
            "Max loss": _dash(card.max_loss if card else None), "Profit T1": _dash(card.profit_t1 if card else None),
            "Win odds": _pct(v.odds.p_target) if v.odds is not None else "n/a",
            "Exp R": "n/a" if e is None else f"{e:+.2f}R",
            "Expected value": _dash(card.expected_value if card else None),
            "Why": why}


def _warn_scan_failures(istate, have_scan: bool) -> None:
    """Scan symbols that gave no verdict (no bars, or a TAKE withheld because it could not be logged)."""
    for sym, why in (istate["ok"][1] if have_scan else ()):
        st.warning(f"Intraday {sym}: {shown(why)}")


def _skipped_row(sym: str, why: str) -> dict:
    return {"Symbol": sym, "Mode": "swing", "Desk says": "SKIP", "Checked": "no verdict", "Built": "—", "Entry": "—",
            "To entry %": None, "Stop": "—", "T1": "—", "T2": "—", "Qty": "—", "Max loss": "—", "Profit T1": "—",
            "Win odds": "—", "Exp R": "—", "Expected value": "—", "Why": why}


def _load_candidates(have_plan: bool, have_scan: bool) -> None:
    refresh = have_plan and have_scan  # both there: the button rebuilds both
    with st.spinner("Building today's plan and the intraday scan — this can take a few minutes..."):
        if refresh or not have_plan:
            state = _attempt("Could not build the plan", session.run_daily_plan, profile, limits, universe(profile),
                             regime=regime_name, tape=tape)
            if "ok" in state:
                state["at"] = _now_hhmm(profile)
            st.session_state[PLAN_KEY] = state
        if refresh or not have_scan:
            syms = st.session_state.get(f"desk_intra_syms_{market}") or universe(profile)
            state = _attempt("Intraday scan failed", session.run_intraday, profile, limits, syms, regime=regime_name)
            if "ok" in state:
                state["at"] = _now_hhmm(profile)
            st.session_state[SCAN_KEY] = state


def _render_skipped(sym: str, why: str) -> None:
    st.error(f"The desk skipped {sym} in today's plan: {shown(why)}")
    ck = ("choose_check", market, _today(profile), _limits_key(limits), sym)
    if st.button("Check it", key="desk_choose_checkit"):
        with st.spinner(f"Checking {sym}..."):
            try:
                st.session_state[ck] = {"ok": session.run_check(profile, sym, Mode.SWING, limits, regime=regime_name,
                                                                desk_entry=True),  # the plan's own entry
                                        "at": _now_hhmm(profile)}
            except Exception as e:  # noqa: BLE001
                st.session_state[ck] = {"error": f"Check failed: {shown(e) if str(e) else type(e).__name__}"}
    cst = st.session_state.get(ck)
    if cst is None:
        st.caption("Press Check it to run the full desk check and open the trade card.")
    elif "error" in cst:
        st.error(cst["error"])
    else:
        st.caption(f"Checked at {cst['at']} (market time)")
        _render_choice(cst["ok"])


HOLDING_UP_TITLE = "Holding up — ready for when the regime turns (not a trade)"


def _render_holding_up(plan_now) -> None:
    """Stocks above their own 50-EMA with the best 60-day return vs the index: a watchlist, never a trade (no action,
    no money, nothing to choose)."""
    st.subheader(HOLDING_UP_TITLE)
    st.caption(f"Above their own 50-EMA and beating the {profile.regime_index} over 60 days, ranked by how much "
               "(up to 10). "
               "Ready for when the regime turns — not a trade: nothing here is checked, sized or logged.")
    held = tuple(getattr(plan_now, "holding_up", ()) or ())
    note = getattr(plan_now, "holding_up_note", None)
    if note:
        st.warning(shown(note))
    elif not held:
        st.caption(strength.EMPTY)
    if held:
        st.dataframe(pd.DataFrame([{"Symbol": s, "vs index (60d)": f"{rs:+.1f} pts"} for s, rs in held]),
                     hide_index=True, width="stretch")


def _render_choice(v: DeskVerdict) -> None:
    _render_card(v, profile, limits, "choose")
    with st.expander("Odds and checks behind the verdict"):
        render_verdict(v, profile, "choose_detail", show_ticket=False)


with tab_choose:
    st.caption("Every candidate from today's plan and the intraday scan, with the money at stake. The desk's "
               "TAKE / WAIT / SKIP is advice: you choose.")
    st.caption("Your call — ranked by the desk's own odds: TAKE, then WAIT, then SKIPs blocked only by the market or "
               "the odds, then the rest; within each, the highest Monte Carlo expectancy (Exp R) first. A SKIP "
               "stays a SKIP. \"quick\" rows are priced on the last close: run a Full check before choosing. A top "
               "TAKE that the full check demoted is not replaced by the next quick one: open that one yourself.")
    pstate, istate = st.session_state.get(PLAN_KEY), st.session_state.get(SCAN_KEY)
    have_plan, have_scan = bool(pstate and "ok" in pstate), bool(istate and "ok" in istate)
    if st.button("Load candidates", key="desk_choose_load"):
        _load_candidates(have_plan, have_scan)
        st.rerun()  # every tab shows the new plan / scan
    for state in (pstate, istate):
        if state and "error" in state:
            st.error(state["error"])
    if not have_plan and not have_scan:
        st.info("Press Load candidates to build today's plan and the intraday scan "
                "(or build them on their own tabs). No trade is a valid answer.")
    else:
        missing = [n for n, ok in (("today's plan", have_plan), ("the intraday scan", have_scan)) if not ok]
        if missing:
            st.caption(f"Not loaded yet: {' and '.join(missing)} — press Load candidates to add it.")
        if have_plan:
            _plan_errors(pstate["ok"])
        try:
            plan_now = pstate["ok"] if have_plan else None
            verdicts = session.merge_candidates(plan_now, istate["ok"][0] if have_scan else ())
            have = {v.plan.symbol for v in verdicts if v.plan.mode is Mode.SWING}
            skipped = tuple((s, w) for s, w in (plan_now.skipped if plan_now is not None else ()) if s not in have)
            rows = [_candidate_row(v) for v in verdicts] + [_skipped_row(s, w) for s, w in skipped]
            labels = ([f"{v.action.value} {v.plan.symbol} · {_mode_label(v.plan)}"
                       + (" (quick check)" if is_quick(v) else "") for v in verdicts]
                      + [f"SKIP {s} · swing (not checked)" for s, _ in skipped])
        except Exception as e:  # noqa: BLE001 - never a blank tab
            st.error(f"Could not list the candidates: {type(e).__name__}: {shown(e)}")
            rows, labels = [], []
        if not rows:
            st.info("No candidates today — no trade is a valid answer.")
            _warn_scan_failures(istate, have_scan)
        else:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                         column_config={"To entry %": st.column_config.NumberColumn(format="%.1f%%")})
            _warn_scan_failures(istate, have_scan)
            pick = st.selectbox("Open a trade card", labels, key="desk_choose_pick")
            i = labels.index(pick) if pick in labels else 0
            with st.container(border=True):
                try:
                    if i < len(verdicts):
                        _render_choice(verdicts[i])
                    else:
                        _render_skipped(*skipped[i - len(verdicts)])
                except Exception as e:  # noqa: BLE001 - never a blank tab
                    st.error(f"Could not show the trade card: {type(e).__name__}: {shown(e)}")
        if have_plan:
            try:
                _render_holding_up(pstate["ok"])
            except Exception as e:  # noqa: BLE001 - never a blank tab
                st.error(f"Could not show the holding-up list: {type(e).__name__}: {shown(e)}")
    st.caption(DISCLAIMER)
