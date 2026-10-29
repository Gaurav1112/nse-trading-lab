"""Trade card for the Desk page: money, levels, ticket gating, charts, projections and research for one verdict.

Used by "Choose a Trade" and "Check a Trade" in pages/0_Desk.py. Every section renders in its own try/except: a
failure shows ``st.error`` with the reason in that section and the rest of the card still renders. Money always goes
through ``profile.money()``. Nothing here places an order; the research panel is display only.
"""
from __future__ import annotations

import html
import time
from dataclasses import replace

import streamlit as st

from nse_backtest.desk import audit, charts, loaders, projections, research
from nse_backtest.desk.models import Action, DeskVerdict, Mode
from nse_backtest.desk.tradecard import build_card

NOT_A_FORECAST = charts.NOT_A_FORECAST
ACTION_COLOR = {"TAKE": "#00C46A", "WAIT": "#FFB800", "SKIP": "#FF3355"}
RESEARCH_TTL_S = 30 * 60
INTRADAY_BARS_TTL_S = 15 * 60
SWING_BARS_SHOWN = 120
OVERRIDES_KEY = "desk_overrides_logged"  # {"input_hash|qty"}: one audit row per pair, never per rerun
ACCOUNT_N = (10, 20, 50)


def _msg(e: Exception) -> str:
    return str(e) or type(e).__name__


def _section(label: str, fn, *args) -> None:
    """Run one card section; an exception becomes this section's own error."""
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001 - one broken section must never blank the card
        st.error(f"{label}: {_msg(e)}")


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


# ---------------------------------------------------------------- data helpers (pure-ish, unit-testable)

def band_closes(bars, mode: Mode):
    """(bars to draw, closes for the range band, reason the band is unavailable or None).

    Swing: the last 120 daily bars. Intraday: the current session's 15-minute bars only (no overnight gap); fewer
    than 30 closes -> the band is unavailable."""
    if mode is Mode.INTRADAY:
        last_day = bars.index[-1].date()
        shown = bars[[ts.date() == last_day for ts in bars.index]]
        if len(shown) < projections.MIN_BAND_CLOSES:
            return shown, None, "not enough intraday bars yet"
        return shown, shown["Close"], None
    shown = bars.tail(SWING_BARS_SHOWN)
    return shown, shown["Close"], None


def _bars(profile, v: DeskVerdict, today: str):
    """Bars for the price chart, loaded lazily and kept in session state (swing per day, intraday 15 minutes).
    A failure is never cached."""
    sym, mode = v.plan.symbol, v.plan.mode
    key = ("card_bars", profile.code, sym, mode.value, today)
    hit = st.session_state.get(key)
    if hit and (mode is Mode.SWING or time.time() - hit["at"] < INTRADAY_BARS_TTL_S):
        return hit["df"]
    df = loaders.load_intraday(profile, sym) if mode is Mode.INTRADAY else loaders.load_daily(profile, sym)
    st.session_state[key] = {"at": time.time(), "df": df}
    return df


def _research(profile, sym: str):
    """Research | Unavailable for a symbol; any exception becomes Unavailable(reason)."""
    try:
        if profile.code == "US":
            return research.us_research(sym)
        acts = loaders.corporate_actions(profile, sym)
        earnings = loaders.next_earnings(profile, sym)[0]
        return research.nse_research(sym, earnings=earnings, actions=acts)
    except Exception as e:  # noqa: BLE001 - shown on the panel as the reason
        return research.Unavailable(_msg(e))


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
    st.caption(f"{card.qty} shares{' (your quantity)' if card.qty_is_override else ''} · notional "
               f"{m(card.notional)} ({card.pct_capital_deployed:.1f}% of capital) · at risk "
               f"{card.pct_capital_at_risk:.1f}% of capital · costs {m(card.cost)}. {card.gap_note}")
    if card.breaches:
        st.error("Breaks your limits:\n\n" + "\n".join(f"- {b}" for b in card.breaches))


def _log_once(v: DeskVerdict, card) -> None:
    logged = st.session_state.setdefault(OVERRIDES_KEY, set())
    k = f"{v.input_hash}|{card.qty}"
    if k not in logged:
        try:
            audit.append_override(replace(v, sizing=card.sizing))  # the quantity the owner chose
        except Exception as e:  # noqa: BLE001 - shown; retried on the next rerun
            st.error(f"Override NOT logged: {_msg(e)}")
            return
        logged.add(k)
    st.caption("Override logged — scored under \"Your overrides\", apart from the desk's picks.")


def _ticket(v: DeskVerdict, card, profile, limits, qty, wkey: str) -> None:
    if v.action is Action.TAKE:
        if card.ticket:
            st.markdown("**Ticket** (type it in yourself — the desk places nothing)")
            st.code("\n".join(card.ticket), language="text")
        else:
            st.info("No ticket without a quantity.")
        return
    if not st.checkbox("I choose this anyway", key=f"{wkey}_choose"):
        st.caption(f"No ticket: the desk says {v.action.value}. Tick \"I choose this anyway\" to see it; "
                   "your choice is logged and scored separately.")
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


def _price(card, profile, today: str, wkey: str) -> None:
    st.markdown("**Price and levels**")
    v = card.verdict
    try:
        bars = _bars(profile, v, today)
    except Exception as e:  # noqa: BLE001 - this section only
        st.error(f"Price chart unavailable: {_msg(e)}")
        return
    shown, closes, why = band_closes(bars, v.plan.mode)
    band = None
    if closes is not None:
        band = projections.price_band(closes, max(1, int(v.plan.max_hold_bars)))
        if band is None:
            why = "not enough clean closes"
    st.plotly_chart(charts.price_chart(shown, card, band, profile), width="stretch", key=f"{wkey}_price")
    if why:
        st.caption(f"Price chart: range band unavailable: {why}")
    st.caption(f"Band: the stock's own past moves over {v.plan.max_hold_bars} bars — {NOT_A_FORECAST}.")


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


def _research_panel(profile, v: DeskVerdict, wkey: str) -> None:
    st.markdown("**News & earnings** (display only — never part of the desk's verdict)")
    sym = v.plan.symbol
    key = ("research", profile.code, sym)
    hit = st.session_state.get(key)
    fresh = hit is not None and time.time() - hit["at"] < RESEARCH_TTL_S
    if st.button("Load news & earnings", key=f"{wkey}_research") and not fresh:
        with st.spinner(f"Fetching news and earnings for {sym}..."):
            hit = {"at": time.time(), "res": _research(profile, sym)}
        st.session_state[key] = hit
        fresh = True
    if not fresh:
        st.caption("Press the button to fetch (it can take ~20 seconds). Cached for 30 minutes.")
        return
    res = hit["res"]
    if isinstance(res, research.Unavailable):
        st.warning(f"Research unavailable: {res.reason}")
        return
    st.caption(f"Source: {res.source} · fetched {res.fetched_at} · display only")
    if not res.items:
        st.caption("No recent items.")
    for it in res.items:
        title = html.escape(it.title)
        url = it.url if it.url and it.url.startswith(("https://", "http://")) else None
        text = f"[{title}]({url})" if url else title
        st.markdown(f"- **{html.escape(it.kind)}** {html.escape(it.when or '')} — {text}")
    for n in res.notes:
        st.caption(n)


# ---------------------------------------------------------------- entry point

def render_card(v: DeskVerdict, profile, limits, key: str, *, today: str, header: bool = True) -> None:
    """One trade card. ``key`` namespaces the widgets; they are also keyed by the verdict's input hash so a new
    verdict starts from the desk's own quantity and an unticked override box."""
    wkey = f"{key}_{v.input_hash[:12]}"
    if header:
        _section("Header", _header, v)
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
    _section("Ticket", _ticket, v, card, profile, limits, qty, wkey)
    _section("Outcome chart", _outcome, card, profile, wkey)
    _section("Price chart", _price, card, profile, today, wkey)
    _section("Account projection", _account, card, profile, limits, wkey)
    _section("Research", _research_panel, profile, v, wkey)
