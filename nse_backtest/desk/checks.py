"""The 17 PASS/WARN/BLOCK safety checks (a short swaps the long-only odds checks for its own). Missing data is never
a PASS."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from nse_backtest.desk import calendars as cal
from nse_backtest.desk.markets import MarketProfile
from nse_backtest.desk.models import Check, Limits, Mode, Odds, ShortEdge, Side, Sizing, Status, TradePlan
from nse_backtest.desk.nse_lists import BanList


@dataclass(frozen=True)
class CheckContext:
    profile: MarketProfile
    limits: Limits
    now: datetime  # tz-aware, in profile.tz
    regime: str | None = None  # TRENDING / MIXED / HOSTILE
    journal: tuple[dict, ...] = ()
    governor_can_trade: bool | None = None
    governor_reasons: tuple[str, ...] = ()
    ban: BanList | None = None
    next_earnings: date | None = None
    earnings_checked: bool = False
    turnover_20d: float | None = None
    today_gap_pct: float | None = None
    day_trades_5d: int | None = 0  # None = count unknowable from the journal
    journal_readable: bool = True
    quote_as_of: datetime | None = None  # tz-aware time of the live quote
    price_is_live: bool = False  # True only when the current price came from a live quote
    # Shorts only (longs never read these): NSE's F&O underlyings (None = could not verify -> short_fno
    # BLOCKs) and the opening bar's volume over its 20-session median (None = not measured -> relative_volume WARNs).
    fno: frozenset[str] | None = None
    or_rel_volume: float | None = None
    or_rel_volume_why: str = ""  # why or_rel_volume is None (shown in the WARN)
    # The pooled ORB-short edge for today (shortcheck; None = unavailable -> short_edge WARNs and caps at WAIT), why it
    # is unavailable, and the market regime of each past day (the short odds replay HOSTILE days only).
    short_edge: ShortEdge | None = None
    short_edge_why: str = ""
    regime_by_day: dict | None = field(default=None, compare=False)


def _entry(e: dict) -> tuple[str, date, float | None] | None:
    """(market, date, pnl) for a journal entry; pnl is None when unreadable.

    Uses `pnl` if it is a finite number, else `net_pnl` if finite, else None.
    Returns None only when the market/date cannot be read at all.
    """
    try:
        raw = next((e[k] for k in ("closed_date", "exit_date", "date") if e.get(k)), None)
        d = date.fromisoformat(str(raw)[:10])
        pnl = _num(e.get("pnl"))
        if pnl is None:
            pnl = _num(e.get("net_pnl"))
        market = str(e.get("market") or "NSE").upper()
    except (TypeError, ValueError, KeyError, AttributeError):
        return None
    return (market, d, pnl)


def unreadable_today(journal, market: str, today: date) -> int:
    """Count journal rows for this market dated today whose P&L cannot be read."""
    rows = (_entry(e) for e in journal)
    return sum(1 for r in rows if r is not None and r[0] == str(market).upper() and r[1] == today and r[2] is None)


def _market_entries(journal, market: str) -> list[tuple[date, float]]:
    rows = [r for r in (_entry(e) for e in journal)
            if r is not None and r[2] is not None and r[0] == str(market).upper()]
    # Stable sort: entries sharing a date keep their original journal order,
    # so the later-listed entry counts as the more recent one in a streak.
    return sorted(((d, p) for _, d, p in rows), key=lambda r: r[0])


def stoploss_streak(journal, market: str, n: int = 3) -> bool:
    rows = _market_entries(journal, market)
    if len(rows) < n:
        return False
    return all(p < 0 for _, p in rows[-n:])


def realised_today(journal, market: str, today: date) -> float:
    return sum(p for d, p in _market_entries(journal, market) if d == today)


def _num(x) -> float | None:
    """Finite float, or None for non-numbers, bools, NaN and infinity."""
    if isinstance(x, bool) or not isinstance(x, (int, float, np.integer, np.floating)):
        return None
    v = float(x)
    return v if math.isfinite(v) else None


def _ck(name: str, status: Status, reason: str) -> Check:
    return Check(name, status, reason)


def _parse_bar(last_bar: str, tz: ZoneInfo) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(last_bar))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


_QUOTE_MAX_AGE_MIN = 20

# The desk's held-out result for swing longs per (market, regime) (% per trade), measured on NSE only (the Picks
# page's 2026 held-out run). Shown in the regime check and in the cost line of an override against it
# (verdict.regime_override_note). A (market, regime) without an entry has no figure and says none.
REGIME_HELD_OUT_PCT = {("NSE", "HOSTILE"): -1.61}
REGIME_HELD_OUT_PERIOD = "held-out 2026"


def signed_pct(x: float) -> str:
    """A percentage with a true minus sign (−1.61), as the desk prints it."""
    return f"{x:.2f}".replace("-", "−")


def _days(n: int) -> str:
    return "day" if n == 1 else "days"


def _closed_name(prof: MarketProfile, d: date) -> str:
    return cal.holiday_name(prof, d) or "weekend"


def _swing_freshness(prof: MarketProfile, bar_day: date, now: datetime) -> Check:
    """Daily data is fresh when no completed trading session is missing after the last bar."""
    P, W, B = Status.PASS, Status.WARN, Status.BLOCK
    today = now.date()
    if bar_day > today:
        return _ck("freshness", W, "Last bar is in the future — check the clock/data")
    year = cal.first_uncovered_year(prof, bar_day, today)
    if year is not None:  # calendar unknown: the old 3-calendar-day rule, never a clean PASS
        sfx = f" (holiday calendar not available for {year}; using a 3-day rule)"
        age = (today - bar_day).days
        if age > 3:
            return _ck("freshness", B, f"Data is stale: last daily bar is {age} {_days(age)} old (limit 3){sfx}")
        return _ck("freshness", W, f"Last daily bar is {age} {_days(age)} old (limit 3){sfx}")
    if bar_day == today and cal.is_trading_day(prof, today) and now.time() < cal.session_close_for(prof, today):
        return _ck("freshness", W, f"Last daily bar {bar_day} is today's unfinished session")
    missed = cal.trading_days_between(prof, bar_day, today)
    if bar_day < today and cal.is_trading_day(prof, today) and now.time() >= cal.session_close_for(prof, today):
        missed += 1  # today's session has closed and its bar is missing
    if missed >= 1:
        return _ck("freshness", B, f"Data is stale: last daily bar {bar_day} misses {missed} trading session(s)")
    return _ck("freshness", P, f"Last daily bar {bar_day} is the latest completed session")


def _entry_window(prof: MarketProfile, now: datetime) -> Check:
    P, W, B = Status.PASS, Status.WARN, Status.BLOCK
    today = now.date()
    known = cal.covered(prof, today)
    sfx = "" if known else f" (holiday calendar not available for {today.year})"
    window = f"{prof.no_entry_before:%H:%M}-{prof.no_entry_after:%H:%M}"
    if not cal.is_trading_day(prof, today):
        return _ck("entry_window", B, f"Market closed today: {_closed_name(prof, today)}{sfx}")
    if cal.is_early_close(prof, today):
        return _ck("entry_window", B, f"Early close today ({cal.session_close_for(prof, today):%H:%M}) — no new intraday entries")
    if not (prof.no_entry_before <= now.time() <= prof.no_entry_after):
        return _ck("entry_window", B, f"{now:%H:%M} is outside the entry window {window}{sfx}")
    return _ck("entry_window", P if known else W, f"{now:%H:%M} is inside the entry window {window}{sfx}")


SHORT_EDGE_UNAVAILABLE = "edge check unavailable"
SHORT_EDGE_UNPROVEN = SHORT_EDGE_UNAVAILABLE  # the cap's reason when no pooled figure exists (verdict.short_edge_cap)
SHORT_REL_VOLUME_MIN = 1.5


def short_edge_check(ctx: CheckContext) -> Check:
    """The ORB-short edge gate: PASS only when today's pooled ORB-short history (shortcheck.pooled_short_edge: the
    eligible universe, HOSTILE days only, 2x slippage) has at least 200 trades and a positive expectancy. Otherwise a
    WARN, and verdict.short_edge_cap holds the short at WAIT. ``proven`` is re-derived from the numbers, never
    trusted. No figure for today (not computed, unreadable cache) = "edge check unavailable"."""
    e = ctx.short_edge
    if not isinstance(e, ShortEdge):
        why = ctx.short_edge_why.strip() if isinstance(ctx.short_edge_why, str) else ""
        return _ck("short_edge", Status.WARN, f"{SHORT_EDGE_UNAVAILABLE} ({why})" if why else SHORT_EDGE_UNAVAILABLE)
    n, exp = _num(e.n), _num(e.expectancy_r)
    if n is None or n < 0:
        return _ck("short_edge", Status.WARN, f"{SHORT_EDGE_UNAVAILABLE} (trade count unreadable)")
    shown = "no expectancy yet" if exp is None else f"{exp:+.2f}R"
    sure = ShortEdge.from_trades(int(n), exp)
    if not sure.proven:
        return _ck("short_edge", Status.WARN,
                   f"edge not proven on the desk's own history yet ({n:.0f} trades, {shown})")
    return _ck("short_edge", Status.PASS,
               f"edge proven on the desk's own history ({n:.0f} trades, {shown} at 2× slippage, HOSTILE days)")


def _short_regime(prof: MarketProfile, regime: str) -> Check:
    """NSE and HOSTILE: market advice (overridable as the owner's call)."""
    if prof.code != "NSE":
        return _ck("short_regime", Status.BLOCK, "Shorts are NSE only")
    if regime != "HOSTILE":
        return _ck("short_regime", Status.BLOCK, f"ORB shorts are for HOSTILE days only (regime {regime or 'unknown'})")
    return _ck("short_regime", Status.PASS, "HOSTILE NSE day: ORB shorts allowed")


def _short_fno(prof: MarketProfile, sym: str, fno: frozenset[str] | None) -> Check:
    """F&O membership (circuit-lock / auction risk outside it): a safety BLOCK, never overridable."""
    B = Status.BLOCK
    if prof.code != "NSE":
        return _ck("short_fno", B, "Shorts are NSE only (no F&O list)")
    if fno is None:
        return _ck("short_fno", B, "could not verify F&O eligibility")
    if sym not in fno:
        return _ck("short_fno", B, f"{sym} is not an F&O stock: intraday shorts are F&O stocks only")
    return _ck("short_fno", Status.PASS, f"{sym} is an F&O stock")


def _relative_volume(ratio, why: str = "") -> Check:
    r = _num(ratio)
    if r is None:
        return _ck("relative_volume", Status.WARN,
                   f"Relative volume of the opening bar could not be measured ({why or 'need 20 earlier sessions'})")
    if r < SHORT_REL_VOLUME_MIN - 1e-12:
        return _ck("relative_volume", Status.WARN,
                   f"Opening bar volume is only {r:.2f}× its 20-session median (want at least {SHORT_REL_VOLUME_MIN}×)")
    return _ck("relative_volume", Status.PASS,
               f"Opening bar volume is {r:.2f}× its 20-session median (at least {SHORT_REL_VOLUME_MIN}×)")


def run_checks(plan: TradePlan, sizing: Sizing, odds: Odds | None, ctx: CheckContext,
               current_price: float | None = None) -> list[Check]:
    """`current_price` is the price the verdict uses (live quote, else the plan's last close)."""
    p, lim, prof = plan, ctx.limits, ctx.profile
    if current_price is None:
        current_price = p.last_close
    mode = Mode(p.mode)
    now = ctx.now
    naive = now.tzinfo is None
    if not naive:
        now = now.astimezone(ZoneInfo(prof.tz))
    swing = mode is Mode.SWING
    short = p.side is Side.SHORT
    out: list[Check] = []
    P, W, B = Status.PASS, Status.WARN, Status.BLOCK

    # 1 regime
    r = (ctx.regime or "").strip().upper() if isinstance(ctx.regime, (str, type(None))) else ""
    unknown = "Regime unknown: could not classify the market, "
    if swing:
        if r == "HOSTILE":
            pct = REGIME_HELD_OUT_PCT.get((prof.code, "HOSTILE"))
            held = "" if pct is None else f" ({REGIME_HELD_OUT_PERIOD}: {signed_pct(pct)}%/trade)"
            out.append(_ck("regime", B, f"Market regime HOSTILE: swing longs historically lose here{held}"))
        elif r == "MIXED":
            out.append(_ck("regime", W, "Market regime MIXED: swing edge is weaker, take smaller or wait"))
        elif r == "TRENDING":
            out.append(_ck("regime", P, "Market regime TRENDING: swing longs have historically worked here"))
        else:
            out.append(_ck("regime", B, "Regime unknown — treated as HOSTILE (swing longs blocked)"))
    elif short:  # eligibility (short_eligibility) BLOCKs a short on any other day
        if r == "HOSTILE":
            out.append(_ck("regime", P, "Market regime HOSTILE: the day ORB breakdown shorts are built for"))
        else:
            out.append(_ck("regime", W, f"Market regime {r or 'unknown'}: ORB shorts are for HOSTILE days only"))
    else:
        if r == "HOSTILE":
            out.append(_ck("regime", W, "Market regime HOSTILE: intraday longs face a falling market, be selective"))
        elif r in ("TRENDING", "MIXED"):
            out.append(_ck("regime", P, f"Market regime {r} does not restrict intraday longs"))
        else:
            out.append(_ck("regime", W, unknown + "be selective"))

    # 2 reward_risk
    try:
        rr = p.rr_t1
    except ZeroDivisionError:
        rr = float("nan")
    d = 2
    if abs(rr - lim.min_rr) < 1e-9:
        pass  # equal: two decimals on both sides
    elif abs(rr - lim.min_rr) < 0.005:  # widen until the two numbers visibly differ
        d = 3
        while d < 9 and f"{rr:.{d}f}" == f"{lim.min_rr:.{d}f}":
            d += 1
    if not (rr >= lim.min_rr):
        out.append(_ck("reward_risk", B, f"Reward:risk 1:{rr:.{d}f} is below minimum 1:{lim.min_rr:.{d}f}"))
    else:
        out.append(_ck("reward_risk", P, f"Reward:risk 1:{rr:.{d}f} meets minimum 1:{lim.min_rr:.{d}f}"))

    # 3 size
    qty = _num(sizing.qty)
    if qty is None:
        out.append(_ck("size", B, "Position size is not a valid number"))
    elif qty < 1:
        why = "Position size is zero" if qty <= 0 else "Position size is below one share"
        out.append(_ck("size", B, f"{why}: {sizing.note}" if sizing.note else why + (" shares" if qty <= 0 else "")))
    else:
        out.append(_ck("size", P, f"Size {sizing.qty} shares, limited by {sizing.binding_limit}"))

    # 4 risk_governor
    if ctx.governor_can_trade is None:
        if ctx.governor_reasons:
            out.append(_ck("risk_governor", W, "Risk governor not evaluated: " + "; ".join(ctx.governor_reasons)))
        else:
            out.append(_ck("risk_governor", W, "Risk governor not evaluated: portfolio limits could not be checked"))
    elif ctx.governor_can_trade is not True:
        why = "; ".join(ctx.governor_reasons) or "no reason given"
        out.append(_ck("risk_governor", B, f"Risk governor says no new trades: {why}"))
    else:
        out.append(_ck("risk_governor", P, "Risk governor allows a new trade"))

    # 5 daily_loss
    lost = -realised_today(ctx.journal, prof.code, now.date())
    cap = lim.capital * lim.daily_loss_limit_pct / 100
    if not (math.isfinite(cap) and cap > 0):
        out.append(_ck("daily_loss", B, "Daily loss limit is not configured"))
    elif lost >= cap:
        out.append(_ck("daily_loss", B, f"Daily loss limit hit: lost {prof.money(lost)} today against a limit of {prof.money(cap)}"))
    elif not ctx.journal_readable:
        out.append(_ck("daily_loss", W, "Could not read the trade journal — daily loss limit not verified"))
    elif (bad := unreadable_today(ctx.journal, prof.code, now.date())) > 0:
        out.append(_ck("daily_loss", W, f"Could not read today's P&L for {bad} journal row(s) — daily loss limit not verified"))
    else:
        out.append(_ck("daily_loss", P, f"Daily loss {prof.money(max(lost, 0.0))} is within the limit of {prof.money(cap)}"))

    # 6 ban_list
    sym = str(p.symbol).strip().upper()
    if sym.startswith("NSE:"):
        sym = sym[4:].strip()
    if prof.exchange and sym.endswith("." + prof.exchange):
        sym = sym[: -len(prof.exchange) - 1]
    for suffix in (".NS", "-EQ"):
        if sym.endswith(suffix):
            sym = sym[: -len(suffix)]
    if short and prof.has_ban_list:  # owner decision: a short in an F&O-ban stock is BLOCKed, and so is not knowing
        if ctx.ban is None or ctx.ban.status not in ("ok", "cached", "stale"):
            out.append(_ck("ban_list", B, "Could not verify the F&O ban list — no intraday shorts without it"))
        elif sym in ctx.ban.symbols:
            out.append(_ck("ban_list", B, f"{sym} is in the F&O ban — no intraday shorts"))
        elif ctx.ban.status == "stale":
            when = "last fetch date unknown" if ctx.ban.as_of is None else f"last fetched {ctx.ban.as_of}"
            out.append(_ck("ban_list", B, f"F&O ban list is stale ({when}) — no intraday shorts without today's list"))
        else:
            out.append(_ck("ban_list", P, f"{sym} is not in the F&O ban list"))
    elif not prof.has_ban_list:
        out.append(_ck("ban_list", P, f"No ban list for {prof.code}"))
    elif ctx.ban is None or ctx.ban.status not in ("ok", "cached", "stale"):
        out.append(_ck("ban_list", W, "Could not verify F&O ban list: check NSE before trading"))
    elif sym in ctx.ban.symbols:  # the ban restricts F&O only: a cash long (CNC or MIS) is allowed
        cash = "cash delivery" if swing else "cash intraday"
        out.append(_ck("ban_list", W, f"{sym} is in the F&O ban — derivatives only; {cash} allowed"))
    elif ctx.ban.status == "stale":
        out.append(_ck("ban_list", W, f"F&O ban list is stale ({'last fetch date unknown' if ctx.ban.as_of is None else 'last fetched ' + str(ctx.ban.as_of)}): {sym} not on it but verify on NSE"))
    else:
        out.append(_ck("ban_list", P, f"{sym} is not in the F&O ban list"))

    # 7 monte_carlo (a short: the short-edge gate instead; the long Monte Carlo does not model a short)
    if short:
        out.append(short_edge_check(ctx))
    elif odds is None:
        out.append(_ck("monte_carlo", B, "Odds could not be computed — no trade without odds"))
    elif (mc := _num(odds.mc_expectancy_r)) is None:
        out.append(_ck("monte_carlo", B, "Monte Carlo expectancy is not a number — no trade without odds"))
    elif mc < 0:
        out.append(_ck("monte_carlo", B, f"Simulated expectancy {mc:+.2f}R is negative"))
    elif mc < 0.1:
        out.append(_ck("monte_carlo", W, f"Simulated expectancy {mc:+.2f}R is thin (under +0.10R)"))
    else:
        out.append(_ck("monte_carlo", P, f"Simulated expectancy {mc:+.2f}R is positive (above +0.10R)"))

    # 8 freshness
    tz = ZoneInfo(prof.tz)
    bar = _parse_bar(p.last_bar, tz)
    if bar is None:
        out.append(_ck("freshness", B, "Could not read the last bar's time: cannot confirm the data is current"))
    elif naive:
        out.append(_ck("freshness", B, "Clock has no timezone — cannot verify freshness"))
    elif swing:
        out.append(_swing_freshness(prof, bar.date(), now))
    else:
        age_min = (now - bar).total_seconds() / 60
        if age_min < -5:
            out.append(_ck("freshness", W, "Last bar is in the future — check the clock/data"))
        elif age_min < 0:
            out.append(_ck("freshness", P, "Last bar is current"))
        elif now - bar > timedelta(minutes=30):
            out.append(_ck("freshness", B, f"Data is stale: last intraday bar is {age_min:.0f} minutes old (limit 30)"))
        else:
            out.append(_ck("freshness", P, f"Last intraday bar is {age_min:.0f} minutes old (limit 30)"))

    # 9 live_price
    cp = _num(current_price)
    if ctx.price_is_live is not True:
        when = bar.date().isoformat() if bar is not None else str(p.last_bar)[:10]
        out.append(_ck("live_price", W, f"Price is the last close of {when} — confirm the live price before entering"))
    elif cp is None or cp <= 0:
        out.append(_ck("live_price", B, "Live price is not a valid number"))
    elif short and cp >= p.stop:
        out.append(_ck("live_price", B, f"Price {prof.money(cp)} is already at or above the stop {prof.money(p.stop)}"))
    elif not short and cp <= p.stop:
        out.append(_ck("live_price", B, f"Price {prof.money(cp)} is already at or below the stop {prof.money(p.stop)}"))
    elif not isinstance(ctx.quote_as_of, datetime) or ctx.quote_as_of.tzinfo is None or naive:
        out.append(_ck("live_price", W, "Live price time unknown — confirm the live price before entering"))
    else:
        as_of = ctx.quote_as_of.astimezone(tz)
        age_min = (now - as_of).total_seconds() / 60
        today = now.date()
        trading = cal.is_trading_day(prof, today)
        in_session = trading and prof.session_open <= now.time() <= cal.session_close_for(prof, today)
        if not trading:
            out.append(_ck("live_price", W, f"Market closed today ({_closed_name(prof, today)}); last trade {as_of:%Y-%m-%d %H:%M}"))
        elif as_of.date() != today:
            out.append(_ck("live_price", W, f"Last trade was {as_of:%Y-%m-%d %H:%M} — market not open today yet; confirm the live price before entering"))
        elif in_session and age_min > _QUOTE_MAX_AGE_MIN:
            out.append(_ck("live_price", W, f"Live price is {age_min:.0f} min old"))
        elif not in_session:
            out.append(_ck("live_price", P, f"Last trade today at {as_of:%H:%M} (market closed)"))
        else:
            out.append(_ck("live_price", P, f"Live price {prof.money(cp)} as of {as_of:%H:%M}"))

    # 10 entry_window
    if swing:
        out.append(_ck("entry_window", P, "Swing entries have no time-of-day window"))
    elif naive:
        out.append(_ck("entry_window", B, "Clock has no timezone — cannot verify freshness"))
    else:
        out.append(_entry_window(prof, now))

    # 11 liquidity
    turn = _num(ctx.turnover_20d)
    if turn is None:
        out.append(_ck("liquidity", W, "Liquidity unavailable: 20-day turnover could not be computed"))
    elif turn < prof.min_turnover:
        out.append(_ck("liquidity", B, f"20-day turnover {prof.money_compact(turn)} is below the minimum {prof.money_compact(prof.min_turnover)}"))
    else:
        out.append(_ck("liquidity", P, f"20-day turnover {prof.money_compact(turn)} meets the minimum {prof.money_compact(prof.min_turnover)}"))

    # 12 earnings
    ne = ctx.next_earnings
    if isinstance(ne, datetime):  # includes pd.Timestamp
        ne = ne.date()
    elif isinstance(ne, str):
        try:
            ne = date.fromisoformat(ne.strip()[:10])
        except ValueError:
            ne = "unreadable"
    if ne == "unreadable" or (ne is not None and not isinstance(ne, date)):
        out.append(_ck("earnings", W, "Could not check earnings date: results may land inside the trade"))
    elif not ctx.earnings_checked:
        out.append(_ck("earnings", W, "Could not check earnings date: results may land inside the trade"))
    elif ne is None:
        out.append(_ck("earnings", P, "No upcoming earnings date found"))
    else:
        days = (ne - now.date()).days
        if swing:
            window = math.ceil(p.max_hold_bars * 7 / 5)
            if days < 0:
                out.append(_ck("earnings", W, f"Earnings date looks stale: {ne} is in the past"))
            elif days <= window:
                out.append(_ck("earnings", W, f"Earnings on {ne} are {days} {_days(days)} away, inside the {window}-day hold window"))
            else:
                out.append(_ck("earnings", P, f"Next earnings on {ne} are outside the {window}-day hold window"))
        elif ne == now.date():
            out.append(_ck("earnings", W, f"Earnings are today ({ne}): expect a violent move"))
        else:
            out.append(_ck("earnings", P, f"Next earnings on {ne} are not today"))

    # 13 setup_history (long-only; a short's history is the short_edge gate)
    if short:
        pass
    elif odds is None and not swing:
        out.append(_ck("setup_history", B, "Research only: setup history could not be computed for this stock"))
    elif odds is None:
        out.append(_ck("setup_history", W, "Setup history unavailable: no past trades of this setup to check"))
    else:
        n, exp = _num(odds.history_n), _num(odds.history_expectancy_r)
        if not swing:
            if n is None or not (n >= 20) or exp is None or exp < 0.2:
                out.append(_ck("setup_history", B, "Research only: this setup hasn't earned +0.2R over ≥20 past sessions on this stock"))
            else:
                out.append(_ck("setup_history", P, f"Setup earned {exp:+.2f}R over {n:.0f} past sessions on this stock"))
        elif n is None or not (n >= 20):
            shown = "an unknown number of" if n is None else f"{n:.0f}"
            out.append(_ck("setup_history", W, f"Only {shown} past samples of this setup (need 20): too few samples to trust"))
        elif exp is None or exp < 0:
            shown = "unknown" if exp is None else f"{exp:+.2f}R"
            out.append(_ck("setup_history", W, f"Setup history expectancy is {shown} over {n:.0f} samples"))
        else:
            out.append(_ck("setup_history", P, f"Setup earned {exp:+.2f}R over {n:.0f} past samples"))

    # 14 stoploss_streak
    if not ctx.journal_readable:
        out.append(_ck("stoploss_streak", W, "Could not read the trade journal — stop-out streak not verified"))
    elif stoploss_streak(ctx.journal, prof.code):
        out.append(_ck("stoploss_streak", W, "3 stop-outs in a row — size halved"))
    else:
        out.append(_ck("stoploss_streak", P, f"No run of 3 straight losses in the {prof.code} journal"))

    # 15 gap
    g = _num(ctx.today_gap_pct)
    if g is None:
        out.append(_ck("gap", W, "Today's gap could not be measured: entry price may be off"))
    elif abs(g) > 2:
        out.append(_ck("gap", W, f"Today's gap of {g:+.1f}% is larger than 2%: entry price may be off"))
    else:
        out.append(_ck("gap", P, f"Today's gap of {g:+.1f}% is within 2%"))

    # 16 robustness (long-only, as setup_history)
    raw_nf = odds.noise_negative_frac if odds is not None else None
    nf = _num(raw_nf)
    if short:
        pass
    elif raw_nf is None:
        out.append(_ck("robustness", W, "Robustness not run — edge not stress-tested"))
    elif nf is None:
        out.append(_ck("robustness", W, "Noise robustness result is not a number: fragility unknown"))
    elif nf > 0.25:
        out.append(_ck("robustness", W, f"Noise test: edge is fragile, {nf:.0%} of noisy replays lose money (limit 25%)"))
    else:
        out.append(_ck("robustness", P, f"Only {nf:.0%} of noisy replays lose money (limit 25%)"))

    # 17 pdt
    guarded = prof.code == "US" and mode is Mode.INTRADAY and lim.pdt_guard
    dt = _num(ctx.day_trades_5d)
    if guarded and dt is not None and dt >= 3:
        out.append(_ck("pdt", B, f"Pattern day trader guard: {dt:.0f} day trades in the last 5 days (limit 3)"))
    elif guarded and not ctx.journal_readable:
        out.append(_ck("pdt", W, "Could not read the trade journal — day-trade count not verified"))
    elif guarded and dt is None:
        out.append(_ck("pdt", W, "Day-trade count not tracked — confirm with your broker"))
    else:
        out.append(_ck("pdt", P, "Pattern day trader guard does not apply or is within limits"))

    # 18-20 shorts only: is it a short day, may this stock be shorted, and has the breakdown volume behind it
    if short:
        out.append(_short_regime(prof, r))
        out.append(_short_fno(prof, sym, ctx.fno))
        out.append(_relative_volume(ctx.or_rel_volume, ctx.or_rel_volume_why))

    return out
