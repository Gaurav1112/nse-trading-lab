"""Trade card for the Desk page: money, levels, ticket gating, charts, projections and research for one verdict.

Used by "Choose a Trade" and "Check a Trade" in pages/0_Desk.py. Every section renders in its own try/except: a
failure shows ``st.error`` with the reason in that section and the rest of the card still renders. Money always goes
through ``profile.money()``. Nothing here places an order; the research panel is display only.
"""
from __future__ import annotations

import html
import math
import time
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st

from nse_backtest.desk import audit, charts, loaders, projections, research, session
from nse_backtest.desk import calendars as cal
from nse_backtest.desk.models import Action, DeskVerdict, Mode
from nse_backtest.desk.textsafe import md_escape, safe_url, shown
from nse_backtest.desk.tradecard import DAILY_LOSS_NOTE, build_card

NOT_A_FORECAST = charts.NOT_A_FORECAST
ACTION_COLOR = {"TAKE": "#00C46A", "WAIT": "#FFB800", "SKIP": "#FF3355"}
RESEARCH_TTL_S = 30 * 60
INTRADAY_BARS_TTL_S = 15 * 60
BAR_FAIL_TTL_S = 60  # a failed bar load is shown, not retried on every rerun, for this long (or until Retry)
BAR_15M = pd.Timedelta(minutes=15)
SWING_BARS_SHOWN = 120
OVERRIDES_KEY = "desk_overrides_logged"  # {input_hash: qty at the first confirmation}: one audit row per verdict
ACCOUNT_N = (10, 20, 50)
STALE_INTRADAY_MIN = 15  # an intraday verdict older than this cannot be acted on from the card: re-check first
RECHECK_KEY = "card_recheck"  # (RECHECK_KEY, key, input_hash) -> {"ok": fresh verdict} | {"error": reason}


def _msg(e: Exception) -> str:
    """An exception as safe display text: no paths or home directory, no Markdown (textsafe.shown)."""
    return shown(str(e) or type(e).__name__)


def _section(label: str, fn, *args) -> None:
    """Run one card section; an exception becomes this section's own error."""
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001 - one broken section must never blank the card
        st.error(f"{label}: {_msg(e)}")


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


# ---------------------------------------------------------------- data helpers (pure-ish, unit-testable)

def market_now(profile) -> datetime:
    """The market's wall clock (tests patch this)."""
    return datetime.now(ZoneInfo(profile.tz))


def built_at(v: DeskVerdict, profile) -> datetime | None:
    """The verdict's created_at in market time (a naive stamp is read as market time); None if unreadable."""
    try:
        t = datetime.fromisoformat(str(v.created_at))
    except ValueError:
        return None
    tz = ZoneInfo(profile.tz)
    return t.replace(tzinfo=tz) if t.tzinfo is None else t.astimezone(tz)


def age_minutes(v: DeskVerdict, profile, now: datetime) -> int | None:
    t = built_at(v, profile)
    return None if t is None else max(0, int((now - t).total_seconds() // 60))


def stale_reason(v: DeskVerdict, profile, now: datetime) -> str | None:
    """Why an intraday verdict must be re-checked before it is acted on (older than 15 minutes, built on another
    day, or now outside the entry window), else None. Swing verdicts are never stale here (their age is shown)."""
    if v.plan.mode is not Mode.INTRADAY:
        return None
    t, age = built_at(v, profile), age_minutes(v, profile, now)
    if t is None or age is None:
        return "This intraday verdict has no readable build time — re-check before choosing it"
    if age > STALE_INTRADAY_MIN or t.date() != now.date():
        return f"This intraday verdict is {age} min old — re-check before choosing it"
    why = cal.outside_entry_window(profile, now)
    if why is not None:
        return f"This intraday verdict was built at {t:%H:%M} and it is now {why} — re-check before choosing it"
    return None


def age_line(v: DeskVerdict, profile, now: datetime) -> str:
    t, age = built_at(v, profile), age_minutes(v, profile, now)
    return "built at an unknown time" if t is None else f"built {t:%H:%M} ({age} min ago)"

def session_returns(bars) -> np.ndarray:
    """Within-session log returns of the Close: each session's first bar starts fresh, so no overnight gap."""
    out = []
    closes = bars["Close"].astype(float)
    for _, grp in closes.groupby(closes.index.date):
        out.append(np.diff(np.log(grp.to_numpy())))
    return np.concatenate(out) if out else np.array([])


def bars_left(last_bar, profile) -> int:
    """15-minute bars that still start before the flat-by of the last bar's day (early closes pull it in)."""
    last = pd.Timestamp(last_bar)
    flat = pd.Timestamp(datetime.combine(last.date(), cal.flat_by_for(profile, last.date())))
    nxt = last + BAR_15M
    return 0 if nxt >= flat else math.ceil((flat - nxt) / BAR_15M)


def chart_inputs(bars, mode: Mode, profile, today: str, *, steps: int | None = None):
    """(bars to draw, range band or None, reason the band is unavailable or None).

    Swing: the last 120 daily bars, band from their closes over ``steps`` bars. Intraday: the current session's
    bars are drawn; the band bootstraps within-session 15-minute returns of the whole loaded history from the last
    close, over the bars left until flat-by. No bars at all -> (None, None, "no bars")."""
    if bars is None or len(bars) == 0:
        return None, None, "no bars"
    if mode is Mode.INTRADAY:
        last = pd.Timestamp(bars.index[-1])
        shown = bars[bars.index.date == last.date()]
        left = bars_left(last, profile)
        if last.date().isoformat() != today or left < 1:
            return shown, None, "session over"
        band = projections.price_band_from_returns(float(bars["Close"].iloc[-1]), session_returns(bars), left)
        if band is None:
            return shown, None, "not enough intraday history yet"
        return shown, replace(band, start=last), None
    shown = bars.tail(SWING_BARS_SHOWN)
    band = projections.price_band(shown["Close"], max(1, int(steps or 1)))
    return shown, band, None if band is not None else "not enough clean closes"


def _bars(profile, v: DeskVerdict, today: str):
    """Bars for the price chart, loaded lazily and kept in session state (swing per day, intraday 15 minutes)."""
    sym, mode = v.plan.symbol, v.plan.mode
    key = ("card_bars", profile.code, sym, mode.value, today)
    hit = st.session_state.get(key)
    if hit and (mode is Mode.SWING or time.time() - hit["at"] < INTRADAY_BARS_TTL_S):
        return hit["df"]
    df = loaders.load_intraday(profile, sym) if mode is Mode.INTRADAY else loaders.load_daily(profile, sym)
    if df is not None and len(df):
        st.session_state[key] = {"at": time.time(), "df": df}
    return df


def _research(profile, sym: str):
    """Research | Unavailable for a symbol; any exception becomes Unavailable(reason)."""
    try:
        if profile.code == "US":
            return research.us_research(sym)
        earnings = loaders.next_earnings(profile, sym)[0]
        try:
            acts, acts_err = loaders.corporate_actions(profile, sym), None
        except Exception as e:  # noqa: BLE001 - a note on the panel; the earnings date still shows
            acts, acts_err = None, str(e) or type(e).__name__
        return research.nse_research(sym, profile, earnings=earnings, actions=acts, actions_error=acts_err)
    except Exception as e:  # noqa: BLE001 - shown on the panel as the reason (escaped when shown)
        return research.Unavailable(str(e) or type(e).__name__)


# ---------------------------------------------------------------- sections

def _header(v: DeskVerdict) -> None:
    color = ACTION_COLOR.get(v.action.value, "#888888")
    st.markdown(f"<div style='font-size:2.2rem;font-weight:800;color:{color};line-height:1.1'>"
                f"{v.action.value} <span style='font-size:1.2rem'>{html.escape(v.plan.symbol)} · "
                f"{v.plan.mode.value}</span></div>", unsafe_allow_html=True)
    st.markdown(f"**{html.escape(v.headline)}**")
    st.caption("The desk's verdict is advice. You make the call and type any ticket in yourself.")


def _reasons(v: DeskVerdict, card) -> None:
    if v.action is Action.TAKE:
        return
    lines = list(card.reasons) or [v.headline]
    st.error(f"The desk says {v.action.value}:\n\n" + "\n".join(f"- {r}" for r in lines))


def _levels(card, profile) -> None:
    m = profile.money
    cols = st.columns(5 if card.entry_limit is not None else 4)
    cols[0].metric("Entry", m(card.entry))
    cols[1].metric("Stop loss", m(card.stop))
    cols[2].metric("Target 1", m(card.target_1))
    cols[3].metric("Target 2", m(card.target_2))
    if card.entry_limit is not None:
        cols[4].metric("Entry limit", m(card.entry_limit))


def _money(card, profile) -> None:
    m = profile.money
    if card.qty == 0:
        loss, p1, p2 = card.per_share
        st.info(card.unavailable_reason)
        st.caption(f"Per share: loss at stop {m(loss)} · profit at T1 {m(p1)} · profit at T2 {m(p2)} · "
                   f"reward:risk {card.rr_t1:.2f}")
        return
    cols = st.columns(5)
    cols[0].metric("Max loss", m(card.max_loss))
    cols[1].metric("Profit at T1", m(card.profit_t1))
    cols[2].metric("Profit at T2", m(card.profit_t2))
    cols[3].metric("Risk:Reward", f"1:{card.rr_t1:.2f}")
    cols[4].metric("Expected value", "n/a (no odds)" if card.expected_value is None else m(card.expected_value))
    if card.fill_note:
        st.caption(f"Profit at T1 and T2 {card.fill_note}.")
    if card.fill_note and card.expected_value is not None:
        st.caption(f"Expected value {card.fill_note}.")
    st.caption(f"{card.qty} shares{' (your quantity)' if card.qty_is_override else ''} · notional "
               f"{m(card.notional)} ({card.pct_capital_deployed:.1f}% of capital) · at risk "
               f"{card.pct_capital_at_risk:.1f}% of capital · costs {m(card.cost)}. {card.gap_note}")
    if card.entry_limit is not None and card.max_loss_at_trigger is not None:
        st.caption(f"Max loss if filled at the trigger: {m(card.max_loss_at_trigger)} (the max loss above assumes "
                   f"the worst fill, at the limit {m(card.entry_limit)})")
    if card.breaches:
        st.error("Breaks your limits:\n\n" + "\n".join(f"- {b}" for b in card.breaches))
        if any("daily loss limit" in b for b in card.breaches):
            st.caption(DAILY_LOSS_NOTE)


def _log_once(v: DeskVerdict, card) -> None:
    """One audit row per verdict (input hash), with the quantity at the first confirmation; later quantity changes
    add no rows."""
    logged = st.session_state.setdefault(OVERRIDES_KEY, {})
    if v.input_hash not in logged:
        try:
            audit.append_override(replace(v, sizing=card.sizing))  # the quantity the owner chose
        except Exception as e:  # noqa: BLE001 - shown; retried on the next rerun
            st.error(f"Override NOT logged: {_msg(e)}")
            return
        logged[v.input_hash] = card.qty
    st.caption(f"Logged once as your override at qty {logged[v.input_hash]} — scored under \"Your overrides\", "
               "apart from the desk's picks.")


def current_verdict(v: DeskVerdict, key: str) -> DeskVerdict:
    """``v``, or the verdict a Re-check under ``key`` replaced it with (following re-checks of re-checks)."""
    for _ in range(8):
        hit = st.session_state.get((RECHECK_KEY, key, v.input_hash))
        if not (hit and "ok" in hit) or hit["ok"].input_hash == v.input_hash:
            break
        v = hit["ok"]
    return v


def recheck_prompt(v: DeskVerdict, profile, limits, key: str, wkey: str, reason: str) -> None:
    """Public form of the stale-verdict prompt (used by the Intraday tab)."""
    _recheck(v, profile, limits, key, wkey, reason)


def _recheck(v: DeskVerdict, profile, limits, key: str, wkey: str, reason: str) -> None:
    """The stale-verdict prompt: no ticket and no override box, only a Re-check that runs the full desk check for
    this symbol and mode and replaces the card's verdict."""
    st.warning(reason)
    rk = (RECHECK_KEY, key, v.input_hash)
    if st.button("Re-check", key=f"{wkey}_recheck"):
        p = v.plan
        levels = (dict(entry=p.entry, stop=p.stop, target=p.target_1) if p.stop_basis == "user" else {})
        with st.spinner(f"Re-checking {p.symbol}..."):
            try:
                st.session_state[rk] = {"ok": session.run_check(profile, p.symbol, p.mode, limits, **levels)}
            except Exception as e:  # noqa: BLE001 - shown under the prompt
                st.session_state[rk] = {"error": _msg(e)}
        if "ok" in st.session_state[rk]:
            st.rerun()
    hit = st.session_state.get(rk)
    if hit is not None and "error" in hit:
        st.error(f"Re-check failed: {hit['error']}")


def _ticket(v: DeskVerdict, card, profile, limits, qty, wkey: str, key: str = "", now: datetime | None = None
            ) -> None:
    reason = stale_reason(v, profile, now or market_now(profile))
    if reason is not None:
        _recheck(v, profile, limits, key, wkey, reason)
        return
    if v.action is Action.TAKE:
        if card.ticket:
            st.markdown("**Ticket** (type it in yourself — the desk places nothing)")
            st.code("\n".join(card.ticket), language="text")
        else:
            st.info("No ticket without a quantity.")
        return
    if v.action is Action.WAIT and v.ticket:
        st.markdown("**Desk's conditional ticket** (the desk's own plan — type it in yourself only if it triggers)")
        st.code("\n".join(v.ticket), language="text")
    if not st.checkbox("I choose this anyway", key=f"{wkey}_choose"):
        now = " to take it now" if v.action is Action.WAIT else ""
        st.caption(f"The desk says {v.action.value}. Tick \"I choose this anyway\"{now} to see your override "
                   "ticket; your choice is logged and scored separately.")
        return
    chosen = build_card(v, profile, limits, qty=qty, override_confirmed=True)
    if not chosen.ticket:
        st.info("Enter a quantity to see your override ticket.")
        return
    st.warning(f"**{chosen.ticket_header}** — type it in yourself; the desk places nothing.")
    st.code("\n".join(chosen.ticket), language="text")
    _log_once(v, chosen)


def _outcome(card, profile, wkey: str) -> None:
    st.markdown("**Money outcome per trade**")
    if card.qty == 0:
        st.info(f"Unavailable: {card.unavailable_reason}")
        return
    om = projections.outcome_money(card)
    if om is None:
        why = "no odds for this trade" if card.verdict.odds is None else "no Monte Carlo replays"
        st.info(f"Unavailable: {why}")
        return
    m = profile.money
    cols = st.columns(3)
    cols[0].metric("Bad case (p5)", m(om.p5))
    cols[1].metric("Median", m(om.median))
    cols[2].metric("Good case (p95)", m(om.p95))
    st.plotly_chart(charts.outcome_chart(om, card, profile), width="stretch", key=f"{wkey}_outcome")
    st.caption(f"P(target) {_pct(om.p_target)} · P(stop) {_pct(om.p_stop)} · P(time exit) {_pct(om.p_time)} "
               f"from the desk's replays — {NOT_A_FORECAST}.")
    if om.limit_slippage is not None:
        st.caption(f"This chart assumes a fill at the trigger; a fill at the limit worsens each outcome by up to "
                   f"{m(om.limit_slippage)}.")


def _price(card, profile, today: str, wkey: str) -> None:
    st.markdown("**Price and levels**")
    v = card.verdict
    fail_key = ("card_bars_fail", profile.code, v.plan.symbol, v.plan.mode.value)
    retry_key = f"{wkey}_retry_bars"
    fail = st.session_state.get(fail_key)
    if fail is not None and time.time() - fail["at"] >= BAR_FAIL_TTL_S:
        st.session_state.pop(fail_key, None)
        fail = None
    if fail is not None:
        if not st.button("Retry", key=retry_key):
            left = max(1, math.ceil(BAR_FAIL_TTL_S - (time.time() - fail["at"])))
            st.error(f"Price chart unavailable: {fail['reason']} (retry in {left}s)")
            return
        st.session_state.pop(fail_key, None)
    try:
        bars = _bars(profile, v, today)
    except Exception as e:  # noqa: BLE001 - this section only; not retried on every rerun
        st.session_state[fail_key] = {"at": time.time(), "reason": _msg(e)}
        st.error(f"Price chart unavailable: {_msg(e)} (retry in {BAR_FAIL_TTL_S}s)")
        if fail is None:  # the Retry button is already on the page when this was a retry
            st.button("Retry", key=retry_key)
        return
    shown, band, why = chart_inputs(bars, v.plan.mode, profile, today, steps=v.plan.max_hold_bars)
    if shown is None:
        st.error(f"Price chart unavailable: {why}")
        return
    st.plotly_chart(charts.price_chart(shown, card, band, profile), width="stretch", key=f"{wkey}_price")
    if why:
        st.caption(f"Price chart: range band unavailable: {why}")
    span = "the bars left until flat-by" if v.plan.mode is Mode.INTRADAY else f"{v.plan.max_hold_bars} bars"
    st.caption(f"Band: the stock's own past moves over {span} — {NOT_A_FORECAST}.")


def _account(card, profile, limits, wkey: str) -> None:
    st.markdown("**Account projection**")
    n = st.radio("Trades to project", ACCOUNT_N, index=ACCOUNT_N.index(20), horizontal=True, key=f"{wkey}_n")
    if card.qty == 0:
        st.info(f"Unavailable: {card.unavailable_reason}")
        return
    odds = card.verdict.odds
    rs, source = (), ""
    if odds is not None and odds.mc_rs:
        rs, source = odds.mc_rs, "Monte Carlo replays"
    elif odds is not None and odds.history_rs:
        rs, source = odds.history_rs, "setup history"
    if not rs:
        st.info("Unavailable: no replay or setup-history results for this trade")
        return
    ap = projections.account_projection(float(limits.capital), card.qty * card.risk_per_share, rs, int(n),
                                        source=source)
    if ap is None:
        st.info("Unavailable: capital or risk is not positive")
        return
    st.plotly_chart(charts.account_chart(ap, profile), width="stretch", key=f"{wkey}_account")
    st.caption(f"Same trade {n} times at this risk, drawn from {source}: {NOT_A_FORECAST}.")
    if card.fill_note:
        st.caption(f"The account projection {card.fill_note} per trade.")


def _research_panel(profile, v: DeskVerdict, wkey: str) -> None:
    st.markdown("**News & earnings** (display only — never part of the desk's verdict)")
    sym = v.plan.symbol
    key = ("research", profile.code, sym)
    hit = st.session_state.get(key)
    failed = hit is not None and isinstance(hit["res"], research.Unavailable)
    fresh = hit is not None and not failed and time.time() - hit["at"] < RESEARCH_TTL_S
    if st.button("Load news & earnings", key=f"{wkey}_research") and not fresh:  # a failure is always retried
        with st.spinner(f"Fetching news and earnings for {sym}..."):
            hit = {"at": time.time(), "res": _research(profile, sym)}
        st.session_state[key] = hit
        failed = isinstance(hit["res"], research.Unavailable)
        fresh = not failed
    if failed:
        st.warning(f"Research unavailable: {shown(hit['res'].reason)} — press the button to try again.")
        return
    if not fresh:
        st.caption("Press the button to fetch (it can take ~20 seconds). Cached for 30 minutes.")
        return
    res = hit["res"]
    st.caption(f"Source: {md_escape(res.source)} · fetched {md_escape(res.fetched_at)} · display only")
    if not res.items:
        st.caption("No recent items.")
    for it in res.items:
        title, url = md_escape(it.title), safe_url(it.url)
        text = f"[{title}]({url})" if url else title
        st.markdown(f"- **{md_escape(it.kind)}** {md_escape(it.when or '')} — {text}")
    for n in res.notes:
        st.caption(shown(n))


# ---------------------------------------------------------------- entry point

def render_card(v: DeskVerdict, profile, limits, key: str, *, today: str, header: bool = True) -> None:
    """One trade card. ``key`` namespaces the widgets; they are also keyed by the verdict's input hash so a new
    verdict starts from the desk's own quantity and an unticked override box. A Re-check of a stale intraday
    verdict replaces it here (session state), until the page builds a new verdict for the row."""
    v = current_verdict(v, key)  # follow re-checks: each replaces the verdict it was made from
    wkey = f"{key}_{v.input_hash[:12]}"
    now = market_now(profile)
    if header:
        _section("Header", _header, v)
    _section("Age", lambda: st.caption(f"Verdict {age_line(v, profile, now)} (market time)"))
    sized = v.sizing.qty
    qty_in = st.number_input("Quantity", min_value=1, value=sized if sized > 0 else None, step=1,
                             key=f"{wkey}_qty", help="Defaults to the desk's size; a bigger quantity lists the "
                                                     "limits it breaks")
    qty = int(qty_in) if qty_in is not None else None
    try:
        card = build_card(v, profile, limits, qty=qty)
    except Exception as e:  # noqa: BLE001 - invalid geometry or quantity: no money, no ticket
        st.error(f"Trade card unavailable: {_msg(e)}")
        return
    _section("Reasons", _reasons, v, card)
    st.markdown("**Levels**")
    _section("Levels", _levels, card, profile)
    st.markdown("**Money**")
    _section("Money", _money, card, profile)
    _section("Ticket", _ticket, v, card, profile, limits, qty, wkey, key, now)
    _section("Outcome chart", _outcome, card, profile, wkey)
    _section("Price chart", _price, card, profile, today, wkey)
    _section("Account projection", _account, card, profile, limits, wkey)
    _section("Research", _research_panel, profile, v, wkey)
