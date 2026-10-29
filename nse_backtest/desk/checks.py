"""The 16 PASS/WARN/BLOCK safety checks. Missing data is never a PASS."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from nse_backtest.desk.markets import MarketProfile
from nse_backtest.desk.models import Check, Limits, Mode, Odds, Sizing, Status, TradePlan
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
    day_trades_5d: int = 0


def _entry(e: dict) -> tuple[str, date, float] | None:
    """(market, date, pnl) for a journal entry, or None if unusable."""
    try:
        raw = next((e[k] for k in ("closed_date", "exit_date", "date") if e.get(k)), None)
        d = date.fromisoformat(str(raw)[:10])
        pnl = e["pnl"] if e.get("pnl") is not None else e.get("net_pnl")
        pnl = float(pnl)
        if math.isnan(pnl):
            return None
    except (TypeError, ValueError, KeyError, AttributeError):
        return None
    return (str(e.get("market") or "NSE"), d, pnl)


def _market_entries(journal, market: str) -> list[tuple[date, float]]:
    rows = [r for r in (_entry(e) for e in journal) if r is not None and r[0] == market]
    return sorted(((d, p) for _, d, p in rows), key=lambda r: r[0])


def stoploss_streak(journal, market: str, n: int = 3) -> bool:
    rows = _market_entries(journal, market)
    if len(rows) < n:
        return False
    return all(p < 0 for _, p in rows[-n:])


def realised_today(journal, market: str, today: date) -> float:
    return sum(p for d, p in _market_entries(journal, market) if d == today)


def _ck(name: str, status: Status, reason: str) -> Check:
    return Check(name, status, reason)


def _parse_bar(last_bar: str, tz: ZoneInfo) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(last_bar))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def run_checks(plan: TradePlan, sizing: Sizing, odds: Odds | None, ctx: CheckContext) -> list[Check]:
    p, lim, prof, now = plan, ctx.limits, ctx.profile, ctx.now
    swing = p.mode is Mode.SWING
    out: list[Check] = []
    P, W, B = Status.PASS, Status.WARN, Status.BLOCK

    # 1 regime
    if swing:
        if ctx.regime == "HOSTILE":
            out.append(_ck("regime", B, "Market regime HOSTILE: swing longs historically lose here (held-out 2026: −1.61%/trade)"))
        elif ctx.regime == "MIXED":
            out.append(_ck("regime", W, "Market regime MIXED: swing edge is weaker, take smaller or wait"))
        elif ctx.regime == "TRENDING":
            out.append(_ck("regime", P, "Market regime TRENDING: swing longs have historically worked here"))
        else:
            out.append(_ck("regime", W, "Regime unknown: could not classify the market, treat swing edge as unconfirmed"))
    else:
        if ctx.regime == "HOSTILE":
            out.append(_ck("regime", W, "Market regime HOSTILE: intraday longs face a falling market, be selective"))
        else:
            out.append(_ck("regime", P, f"Market regime {ctx.regime or 'unknown'} does not restrict intraday longs"))

    # 2 reward_risk
    try:
        rr = p.rr_t1
    except ZeroDivisionError:
        rr = float("nan")
    if not (rr >= lim.min_rr):
        out.append(_ck("reward_risk", B, f"Reward:risk 1:{rr:.2f} is below minimum 1:{lim.min_rr:.2f}"))
    else:
        out.append(_ck("reward_risk", P, f"Reward:risk 1:{rr:.2f} meets minimum 1:{lim.min_rr:.2f}"))

    # 3 size
    if sizing.qty < 1:
        out.append(_ck("size", B, f"Position size is zero: {sizing.note}" if sizing.note else "Position size is zero shares"))
    else:
        out.append(_ck("size", P, f"Size {sizing.qty} shares, limited by {sizing.binding_limit}"))

    # 4 risk_governor
    if ctx.governor_can_trade is None:
        out.append(_ck("risk_governor", W, "Risk governor not evaluated: portfolio limits could not be checked"))
    elif ctx.governor_can_trade is False:
        why = "; ".join(ctx.governor_reasons) or "no reason given"
        out.append(_ck("risk_governor", B, f"Risk governor says no new trades: {why}"))
    else:
        out.append(_ck("risk_governor", P, "Risk governor allows a new trade"))

    # 5 daily_loss
    lost = -realised_today(ctx.journal, prof.code, now.date())
    cap = lim.capital * lim.daily_loss_limit_pct / 100
    if lost >= cap:
        out.append(_ck("daily_loss", B, f"Daily loss limit hit: lost {prof.money(lost)} today against a limit of {prof.money(cap)}"))
    else:
        out.append(_ck("daily_loss", P, f"Daily loss {prof.money(max(lost, 0.0))} is within the limit of {prof.money(cap)}"))

    # 6 ban_list
    sym = p.symbol.upper()
    if sym.endswith("." + prof.exchange) and prof.exchange:
        sym = sym[: -len(prof.exchange) - 1]
    if not prof.has_ban_list:
        out.append(_ck("ban_list", P, f"No ban list for {prof.code}"))
    elif ctx.ban is None or ctx.ban.status == "unavailable":
        out.append(_ck("ban_list", W, "Could not verify F&O ban list: check NSE before trading"))
    elif sym in ctx.ban.symbols:
        out.append(_ck("ban_list", B, f"{sym} is in the NSE F&O ban list: no fresh positions allowed"))
    elif ctx.ban.status == "stale":
        out.append(_ck("ban_list", W, f"F&O ban list is stale (last fetched {ctx.ban.as_of}): {sym} not on it but verify on NSE"))
    else:
        out.append(_ck("ban_list", P, f"{sym} is not in the F&O ban list"))

    # 7 monte_carlo
    if odds is None:
        out.append(_ck("monte_carlo", W, "Monte Carlo odds unavailable: no simulated expectancy to check"))
    elif odds.mc_expectancy_r < 0:
        out.append(_ck("monte_carlo", B, f"Simulated expectancy {odds.mc_expectancy_r:+.2f}R is negative"))
    elif odds.mc_expectancy_r < 0.1:
        out.append(_ck("monte_carlo", W, f"Simulated expectancy {odds.mc_expectancy_r:+.2f}R is thin (under +0.10R)"))
    else:
        out.append(_ck("monte_carlo", P, f"Simulated expectancy {odds.mc_expectancy_r:+.2f}R is healthy"))

    # 8 freshness
    tz = ZoneInfo(prof.tz)
    bar = _parse_bar(p.last_bar, tz)
    if bar is None:
        out.append(_ck("freshness", B, "Could not read the last bar's time: cannot confirm the data is current"))
    elif swing:
        age = (now.date() - bar.date()).days
        if age > 3:
            out.append(_ck("freshness", B, f"Data is stale: last daily bar is {age} days old (limit 3)"))
        else:
            out.append(_ck("freshness", P, f"Last daily bar is {age} days old (limit 3)"))
    else:
        age_min = (now - bar).total_seconds() / 60
        if now - bar > timedelta(minutes=30):
            out.append(_ck("freshness", B, f"Data is stale: last intraday bar is {age_min:.0f} minutes old (limit 30)"))
        else:
            out.append(_ck("freshness", P, f"Last intraday bar is {age_min:.0f} minutes old (limit 30)"))

    # 9 entry_window
    if swing:
        out.append(_ck("entry_window", P, "Swing entries have no time-of-day window"))
    elif now.weekday() >= 5:
        out.append(_ck("entry_window", B, f"Market is closed: {now:%A} is a weekend"))
    elif not (prof.no_entry_before <= now.time() <= prof.no_entry_after):
        out.append(_ck("entry_window", B, f"{now:%H:%M} is outside the entry window {prof.no_entry_before:%H:%M}-{prof.no_entry_after:%H:%M}"))
    else:
        out.append(_ck("entry_window", P, f"{now:%H:%M} is inside the entry window {prof.no_entry_before:%H:%M}-{prof.no_entry_after:%H:%M}"))

    # 10 liquidity
    if ctx.turnover_20d is None:
        out.append(_ck("liquidity", W, "Liquidity unknown: 20-day turnover could not be computed"))
    elif ctx.turnover_20d < prof.min_turnover:
        out.append(_ck("liquidity", B, f"20-day turnover {prof.money(ctx.turnover_20d)} is below the minimum {prof.money(prof.min_turnover)}"))
    else:
        out.append(_ck("liquidity", P, f"20-day turnover {prof.money(ctx.turnover_20d)} meets the minimum {prof.money(prof.min_turnover)}"))

    # 11 earnings
    if not ctx.earnings_checked:
        out.append(_ck("earnings", W, "Could not check earnings date: results may land inside the trade"))
    elif ctx.next_earnings is None:
        out.append(_ck("earnings", P, "No upcoming earnings date found"))
    else:
        days = (ctx.next_earnings - now.date()).days
        if swing:
            window = math.ceil(p.max_hold_bars * 7 / 5)
            if 0 <= days <= window:
                out.append(_ck("earnings", W, f"Earnings on {ctx.next_earnings} are {days} days away, inside the {window}-day hold window"))
            else:
                out.append(_ck("earnings", P, f"Next earnings on {ctx.next_earnings} are outside the {window}-day hold window"))
        elif ctx.next_earnings == now.date():
            out.append(_ck("earnings", W, f"Earnings are today ({ctx.next_earnings}): expect a violent move"))
        else:
            out.append(_ck("earnings", P, f"Next earnings on {ctx.next_earnings} are not today"))

    # 12 setup_history
    if odds is None:
        out.append(_ck("setup_history", W, "Setup history unavailable: no past trades of this setup to check"))
    else:
        n, exp = odds.history_n, odds.history_expectancy_r
        if not swing:
            if n < 20 or exp is None or exp < 0.2:
                out.append(_ck("setup_history", B, "Research only: this setup hasn't earned +0.2R over ≥20 past sessions on this stock"))
            else:
                out.append(_ck("setup_history", P, f"Setup earned {exp:+.2f}R over {n} past sessions on this stock"))
        elif n < 20:
            out.append(_ck("setup_history", W, f"Only {n} past samples of this setup (need 20): too few samples to trust"))
        elif exp is None or exp < 0:
            shown = "unknown" if exp is None else f"{exp:+.2f}R"
            out.append(_ck("setup_history", W, f"Setup history expectancy is {shown} over {n} samples"))
        else:
            out.append(_ck("setup_history", P, f"Setup earned {exp:+.2f}R over {n} past samples"))

    # 13 stoploss_streak
    if stoploss_streak(ctx.journal, prof.code):
        out.append(_ck("stoploss_streak", W, "3 stop-outs in a row — size halved"))
    else:
        out.append(_ck("stoploss_streak", P, f"No run of 3 straight losses in the {prof.code} journal"))

    # 14 gap
    g = ctx.today_gap_pct
    if g is not None and abs(g) > 2:
        out.append(_ck("gap", W, f"Today's gap of {g:+.1f}% is larger than 2%: entry price may be off"))
    elif g is None:
        out.append(_ck("gap", P, "No gap data for today; nothing flagged"))
    else:
        out.append(_ck("gap", P, f"Today's gap of {g:+.1f}% is within 2%"))

    # 15 robustness
    nf = odds.noise_negative_frac if odds is not None else None
    if nf is None:
        out.append(_ck("robustness", P, "Noise robustness test not run"))
    elif nf > 0.25:
        out.append(_ck("robustness", W, f"Edge is fragile: {nf:.0%} of noisy replays lose money (limit 25%)"))
    else:
        out.append(_ck("robustness", P, f"Only {nf:.0%} of noisy replays lose money (limit 25%)"))

    # 16 pdt
    if prof.code == "US" and p.mode is Mode.INTRADAY and lim.pdt_guard and ctx.day_trades_5d >= 3:
        out.append(_ck("pdt", B, f"Pattern day trader guard: {ctx.day_trades_5d} day trades in the last 5 days (limit 3)"))
    else:
        out.append(_ck("pdt", P, "Pattern day trader guard does not apply or is within limits"))

    return out
