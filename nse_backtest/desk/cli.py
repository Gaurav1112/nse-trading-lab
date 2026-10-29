"""Command line: python -m nse_backtest.desk {check,plan,positions,intraday,settings,scoreboard}. Places nothing."""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
from dataclasses import replace
from datetime import date, datetime
from enum import Enum
from zoneinfo import ZoneInfo

from . import audit, loaders, session
from . import calendars as cal
from .markets import get_profile, universe
from .models import Action, DeskVerdict, Mode, summary_fields
from .positions import review_positions
from .tradecard import DAILY_LOSS_NOTE, build_card
from .scoreboard_io import SCOREBOARD_COVERAGE, SCOREBOARD_DISCLAIMER, AuditReadError

FOOTER = "Not financial advice. Nothing was placed. Paper-trade until your record matches the odds."
PLAN_UNIVERSE_LIMIT: int | None = None  # None = whole universe


# ---------------------------------------------------------------- output helpers

def to_jsonable(x):
    """Dataclasses, enums, dates, numpy scalars and non-finite floats to JSON-safe values."""
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return {k: to_jsonable(v) for k, v in summary_fields(x).items()}  # bulky fields (Odds.mc_rs) left out
    if isinstance(x, Enum):
        return to_jsonable(x.value)
    if isinstance(x, dict):
        return {str(k): to_jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in x]
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    if hasattr(x, "item") and not isinstance(x, (str, bytes)):  # numpy scalar
        try:
            return to_jsonable(x.item())
        except (ValueError, TypeError):
            pass
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if x is None or isinstance(x, (str, int, bool)):
        return x
    return str(x)


def _emit_json(obj) -> None:
    print(json.dumps(to_jsonable(obj), indent=2))


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _r(x) -> str:
    return "n/a" if x is None else f"{x:+.2f}R"


def format_verdict(v: DeskVerdict, profile, *, ticket: bool = True) -> list[str]:
    m, p, s, o = profile.money, v.plan, v.sizing, v.odds
    lines = [v.headline, ""]
    lines.append(f"Plan ({p.mode.value}): entry {m(p.entry)}  stop {m(p.stop)}  T1 {m(p.target_1)}  "
                 f"T2 {m(p.target_2)}  R {m(p.risk_per_share)}/share  basis: {p.stop_basis}")
    lines.append(f"Sizing: qty {s.qty}, binding limit {s.binding_limit}, risk {m(s.risk_amount)}, "
                 f"notional {m(s.notional)}, costs {m(s.costs)}. {s.note}")
    if o is None:
        lines.append("Odds: unavailable (not enough history for a reliable estimate)")
    else:
        lines.append(f"Odds ({o.replays} replays): P(target) {_pct(o.p_target)}  P(stop) {_pct(o.p_stop)}  "
                     f"P(time) {_pct(o.p_time)}  MC expectancy {_r(o.mc_expectancy_r)}")
        win = "n/a" if o.history_win_pct is None else f"{o.history_win_pct:.0f}%"
        lines.append(f"History: n={o.history_n}  win {win}  expectancy {_r(o.history_expectancy_r)}")
        dd = "n/a" if o.dd_p95_r is None else f"{o.dd_p95_r:.1f}R"
        streak = "n/a" if o.p_5_loss_streak is None else f"{o.p_5_loss_streak * 100:.1f}%"
        lines.append(f"Worst drawdown (p95): {dd}  P(5 losses in a row): {streak}")
        if o.noise_expectancy_min is not None and o.noise_expectancy_max is not None:
            lines.append(f"Candle-noise expectancy range: {_r(o.noise_expectancy_min)} to {_r(o.noise_expectancy_max)}")
    lines += ["", "Checks:"]
    lines += [f"  {c.status.value} {c.name} — {c.reason}" for c in v.checks]
    if ticket and v.ticket:
        lines += ["", "Ticket (type it in yourself):"] + [f"  {t}" for t in v.ticket]
    return lines


def format_money(card, profile) -> list[str]:
    """The card's money block: max loss, profit at T1/T2, reward:risk, expected value and any limit breaches."""
    m = profile.money
    if card.qty == 0:
        loss, p1, p2 = card.per_share
        return [f"Money: unavailable — {card.unavailable_reason} (--qty N).",
                f"  Per share: loss at stop {m(loss)}, profit at T1 {m(p1)}, profit at T2 {m(p2)}"
                f"  reward:risk {card.rr_t1:.2f}"]
    ev = "n/a (no odds)" if card.expected_value is None else m(card.expected_value)
    lines = [f"Money ({card.qty} shares{', your quantity' if card.qty_is_override else ''}):",
             f"  max loss {m(card.max_loss)} ({card.gap_note[0].lower() + card.gap_note[1:]})",
             f"  profit at T1 {m(card.profit_t1)}  profit at T2 {m(card.profit_t2)}"
             + (f" ({card.fill_note})" if card.fill_note else ""),
             f"  reward:risk {card.rr_t1:.2f}  expected value {ev}"
             + (f" ({card.fill_note})" if card.fill_note and card.expected_value is not None else ""),
             f"  notional {m(card.notional)} ({card.pct_capital_deployed:.1f}% of capital), "
             f"at risk {card.pct_capital_at_risk:.1f}% of capital, costs {m(card.cost)}"]
    if card.entry_limit is not None and card.max_loss_at_trigger is not None:
        lines.append(f"  max loss if filled at the trigger {m(card.max_loss_at_trigger)} (the max loss above "
                     f"assumes the worst fill, at the limit {m(card.entry_limit)})")
    if card.breaches:
        lines += ["  Breaks your limits:"] + [f"    - {b}" for b in card.breaches]
        if any("daily loss limit" in b for b in card.breaches):
            lines.append(f"  {DAILY_LOSS_NOTE}")
    return lines


def _finish(lines: list[str]) -> int:
    print("\n".join(lines + ["", FOOTER]))
    return 0


def _stderr_warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def _limits(profile, as_json: bool = False):
    """(limits, warning lines). A corrupt settings file falls back to defaults, loudly."""
    limits, fell_back = session.limits_with_status(profile)
    if not fell_back:
        return limits, []
    msg = f"WARNING: settings file unreadable — using default capital {profile.money(limits.capital)}"
    if as_json:
        print(msg, file=sys.stderr)
        return limits, []
    return limits, [msg, ""]


# ---------------------------------------------------------------- commands

def market_now(profile) -> datetime:
    """The market's wall clock (tests patch this)."""
    return datetime.now(ZoneInfo(profile.tz))


def _log_override(v: DeskVerdict, card) -> None:
    """The owner's override row, with the quantity they chose."""
    audit.append_override(replace(v, sizing=card.sizing))


def cmd_check(a, profile) -> int:
    limits, warn = _limits(profile, a.json)
    if a.qty is not None and a.qty < 1:
        raise ValueError(f"qty must be a whole number of at least 1, got {a.qty}")
    v = session.run_check(profile, a.symbol, Mode(a.mode), limits, entry=a.entry, stop=a.stop,
                          target=a.target, on_warning=_stderr_warn)
    taken = v.action is Action.TAKE
    choose = bool(a.choose) and not taken
    refused = None
    if choose and v.plan.mode is Mode.INTRADAY:  # the scoreboard would score it as logged outside the window
        why = cal.outside_entry_window(profile, market_now(profile))
        if why is not None:
            refused = f"--choose refused: an intraday override is only taken inside the entry window; it is {why}."
            choose = False
    card = build_card(v, profile, limits, qty=a.qty, override_confirmed=choose)
    logged = False
    if choose and card.ticket:
        _log_override(v, card)
        logged = True
    if a.json:
        d = to_jsonable(v)
        d["card"] = {k: val for k, val in to_jsonable(card).items() if k != "verdict"}
        d["override_logged"] = logged
        if refused:
            d["override_refused"] = refused
        _emit_json(d)
        return 0
    lines = warn + format_verdict(v, profile, ticket=False) + [""] + format_money(card, profile)
    if not taken and card.reasons:
        lines += ["", f"The desk said {v.action.value} because:"] + [f"  {r}" for r in card.reasons]
    if v.action is Action.WAIT and v.ticket:  # the desk's own conditional ticket, no override needed
        lines += ["", "Desk's conditional ticket (type it in yourself only if it triggers):"]
        lines += [f"  {t}" for t in v.ticket]
    if taken:
        lines += ["", "Ticket (type it in yourself):"] + [f"  {t}" for t in card.ticket]
        if a.choose:
            lines += ["", "--choose ignored: the desk already says TAKE."]
    elif card.ticket:
        lines += ["", f"{card.ticket_header} (type it in yourself):"] + [f"  {t}" for t in card.ticket]
        lines += ["Override logged — it is scored under \"Your overrides\", apart from the desk's picks."]
    elif refused:
        lines += ["", refused]
    elif choose:
        lines += ["", "No ticket: the desk sized 0 shares — add --qty N to choose this trade."]
    else:
        now = " now" if v.action is Action.WAIT else ""
        lines += ["", f"The desk says {v.action.value}. Add --choose to take it{now} anyway "
                      "(logged as your override)."]
    return _finish(lines)


def _format_row(v: DeskVerdict, *, why: bool = False) -> str:
    e = v.odds.mc_expectancy_r if v.odds is not None else None
    row = f"  {v.headline}  [exp {_r(e)}]"
    if why:
        blocks = [c.name for c in v.blocks()]
        warns = [c.name for c in v.warns()]
        if blocks or warns:
            row += f"\n    blocked by: {', '.join(blocks) or 'none'}; warnings: {', '.join(warns) or 'none'}"
    return row


def cmd_plan(a, profile) -> int:
    limits, warn = _limits(profile, a.json)
    syms = universe(profile)[:PLAN_UNIVERSE_LIMIT]
    plan = session.run_daily_plan(profile, limits, syms, _stderr_warn)
    if a.json:
        _emit_json(plan)
        return 0
    lines = warn + [f"Daily plan {profile.code} — regime {plan.regime or 'UNKNOWN'}: {plan.regime_text}"]
    if plan.flip_conditions:
        lines += ["Regime flips if:"] + [f"  - {c}" for c in plan.flip_conditions]
    lines += ["", "TAKE:"] + ([_format_row(v) for v in plan.take] or ["  none today — no trade is a valid answer"])
    lines += ["", "WATCH:"] + ([_format_row(v, why=True) for v in plan.watch] or ["  none"])
    for v in plan.take:
        lines += [""] + format_verdict(v, profile)
    if plan.unavailable:
        lines += ["", "No data: " + ", ".join(plan.unavailable)]
    lines += ["", f"Skipped {len(plan.skipped)} symbols."]
    return _finish(lines)


def cmd_positions(a, profile) -> int:
    positions = loaders.market_positions(profile)
    open_symbols = sorted({str(p["symbol"]) for p in positions if p.get("symbol") and not p.get("closed")})
    frames = session.load_frames(profile, open_symbols, _stderr_warn)
    actions = review_positions(positions, {k: v for k, v in frames.items() if v is not None}, profile)
    if a.json:
        _emit_json(actions)
        return 0
    lines = [f"Open positions {profile.code}: {len(actions)}"]
    for x in actions:
        px = "n/a" if x.current_price is None else profile.money(x.current_price)
        r = "n/a" if x.r_so_far is None else f"{x.r_so_far:+.2f}R"
        sug = "" if x.suggested_stop is None else f"  suggested stop {profile.money(x.suggested_stop)}"
        entry = "n/a" if x.entry is None else profile.money(x.entry)
        stop = "n/a" if x.stop is None else profile.money(x.stop)
        lines.append(f"  {x.action:<16} {x.symbol}  now {px}  entry {entry}  "
                     f"stop {stop}{sug}  {r}  — {x.reason}")
    return _finish(lines)


def cmd_intraday(a, profile) -> int:
    limits, warn = _limits(profile, a.json)
    syms = ([s.strip().upper() for s in a.symbols.split(",") if s.strip()]
            if a.symbols else universe(profile)[:15])
    verdicts, failures = session.run_intraday(profile, limits, syms, on_warning=_stderr_warn)
    if a.json:
        _emit_json({"verdicts": verdicts, "failures": failures})
        return 0
    lines = warn + [f"Intraday opening-range scan {profile.code} ({len(syms)} symbols)"]
    for v in verdicts:
        if v.action is Action.SKIP:
            lines.append(_format_row(v))
        else:
            lines += [""] + format_verdict(v, profile)
    if not verdicts:
        lines.append("  no setups scanned")
    if failures:
        lines += ["", "Not scanned:"] + [f"  {s}: {why}" for s, why in failures]
    return _finish(lines)


def cmd_settings(a, profile) -> int:
    cur, warn = _limits(profile, a.json)
    changes = {}
    if a.capital is not None:
        changes["capital"] = a.capital
    if a.risk_pct is not None:
        changes["risk_per_trade_pct"] = a.risk_pct
    if a.max_position_pct is not None:
        changes["max_position_pct"] = a.max_position_pct
    if a.daily_loss_pct is not None:
        changes["daily_loss_limit_pct"] = a.daily_loss_pct
    if a.pdt_guard is not None:
        changes["pdt_guard"] = a.pdt_guard == "on"
    if changes:
        cur = replace(cur, **changes)
        loaders.save_limits(profile, cur)
    if a.json:
        _emit_json({"market": profile.code, "limits": cur})
        return 0
    lines = warn + [f"Settings {profile.code}" + (" (saved)" if changes else ""),
             f"  capital {profile.money(cur.capital)}",
             f"  risk per trade {cur.risk_per_trade_pct:g}%",
             f"  max position {cur.max_position_pct:g}%",
             f"  cash buffer {cur.cash_buffer_pct:g}%",
             f"  daily loss limit {cur.daily_loss_limit_pct:g}%",
             f"  min reward:risk {cur.min_rr:g}",
             f"  PDT guard {'on' if cur.pdt_guard else 'off'}"]
    return _finish(lines)


def _num_r(x) -> str:
    return "n/a" if x is None else f"{x:+.2f}R"


def _projected_line(outcomes, s) -> str:
    """"Projected xR vs realised yR over n trades" for one (record, market, mode) group: the trades that had a
    projection, so both sums cover the same trades."""
    from .projections import projected_vs_real, resolved_without_r
    group = [o for o in outcomes if (o.candidate.record, o.candidate.market, o.candidate.mode) ==
             (s.record, s.market, s.mode)]
    _, _, pairs, n_without = projected_vs_real(group)
    n_no_r = resolved_without_r(group)
    no_r = f"{n_no_r} resolved trade{'s' if n_no_r != 1 else ''} without a realised R, left out" if n_no_r else ""
    if not pairs:
        return "  No projections to compare yet" + (f" ({no_r})" if no_r else "")
    proj, real = sum(p for p, _ in pairs), sum(r for _, r in pairs)
    parts = [x for x in (f"{n_without} without a projection" if n_without else "", no_r) if x]
    extra = f" ({'; '.join(parts)})" if parts else ""
    n = len(pairs)
    return f"  Projected {proj:+.2f}R vs realised {real:+.2f}R over {n} trade{'s' if n != 1 else ''}{extra}"


def format_scoreboard(res, market_label: str) -> list[str]:
    """Text scoreboard: disclaimer first; a "Desk picks" and a "Your overrides" section (never pooled), each with
    per (market, mode) the verdict line first, then the numbers."""
    from .markets import get_profile as _gp
    from .scoreboard import RECORD_LABELS
    lines = [SCOREBOARD_DISCLAIMER, SCOREBOARD_COVERAGE]
    if getattr(res, "note", None):
        lines.append(res.note)
    if res.rejects:
        n = len(res.rejects)
        lines += [f"Rejected {n} audit line{'s' if n != 1 else ''}:"]
        lines += [f"  line {ln}: {why}" for ln, why in res.rejects]
    if not res.outcomes and res.rejects:
        lines.append(f"No valid TAKE verdicts for {market_label} ({len(res.rejects)} unreadable — see below)")
    elif not res.outcomes:
        lines.append(f"No desk picks or overrides logged yet for {market_label} — run plan, check or the intraday "
                     "scan, or choose a trade")
    for record, label in RECORD_LABELS.items() if res.outcomes else ():
        stats = [s for s in res.stats if s.record == record]
        lines += ["", label]
        if not stats:
            lines.append(f"  No {'overrides' if record == 'override' else 'desk picks'} logged yet.")
        for s in stats:
            win = "n/a" if s.win_pct is None else f"{s.win_pct:.1f}%"
            ci = "n/a" if s.ci_low is None else f"{_num_r(s.ci_low)} to {_num_r(s.ci_high)}"
            dd = "n/a" if s.max_dd_r is None else f"{s.max_dd_r:.2f}R"
            cov = "n/a" if s.pred_coverage is None else f"{s.pred_coverage * 100:.0f}%"
            lines += [f"{s.market} {s.mode.value}: {s.verdict}",
                      f"  trades {s.n}  win {win}  mean realised {_num_r(s.mean_r)} vs predicted MC "
                      f"{_num_r(s.mean_pred_mc_r)} / history {_num_r(s.mean_pred_hist_r)}",
                      f"  95% CI of mean realised {ci}  max drawdown {dd}",
                      f"  not filled {s.not_filled}  open {s.open}  unavailable {s.unavailable}  "
                      f"prediction coverage {cov}",
                      _projected_line(res.outcomes, s)]
    if res.outcomes:
        lines += ["", "Trades:",
                  f"  {'date':<10} {'record':<8} {'mkt':<4} {'symbol':<12} {'mode':<9} {'status':<16} {'fill':>14} "
                  f"{'exit':>14} {'real R':>8} {'MC R':>8}  note"]
        for o in res.outcomes:
            c = o.candidate
            m = _gp(c.market).money
            fill = "-" if o.fill_price is None else m(o.fill_price)
            exit_ = "-" if o.exit_price is None else m(o.exit_price)
            real = _num_r(o.realised_r) if o.realised_r is not None else (
                f"({_num_r(o.mtm_r)})" if o.mtm_r is not None else "-")
            day = c.created_at.astimezone(ZoneInfo(_gp(c.market).tz)).date().isoformat()
            lines.append(f"  {day:<10} {c.record:<8} {c.market:<4} {c.symbol:<12} {c.mode.value:<9} "
                         f"{o.status.value:<16} "
                         f"{fill:>14} {exit_:>14} {real:>8} {_num_r(c.mc_expectancy_r):>8}  {o.note}")
        if any(o.status.value == "open" and o.mtm_r is not None for o in res.outcomes):
            lines.append("  (R in brackets = open trade marked to the last close; not counted in the stats)")
    if res.cache_note:
        lines += ["", f"Cache: {res.cache_note}"]
    return lines


def cmd_scoreboard(a, profile) -> int:
    market = str(a.market).strip().upper()
    label = "all markets" if market == "ALL" else get_profile(market).code  # ValueError -> exit 2
    try:
        res = session.run_scoreboard(None if market == "ALL" else label, on_warning=_stderr_warn)
    except (AuditReadError, RuntimeError) as e:
        print(f"scoreboard: {e}", file=sys.stderr)
        return 2
    if a.json:
        _emit_json(res)
        return 0
    return _finish(format_scoreboard(res, label))  # 0 even when the verdict is "Losing": it is information


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m nse_backtest.desk",
                                 description="Trade desk: sized TAKE / WAIT / SKIP decisions for NSE and US. "
                                             "Places nothing.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--market", default="NSE", help="NSE or US (default NSE)")
        p.add_argument("--json", action="store_true", help="print JSON instead of text")

    c = sub.add_parser("check", help="check one symbol")
    c.add_argument("symbol")
    common(c)
    c.add_argument("--mode", choices=["swing", "intraday"], default="swing")
    c.add_argument("--entry", type=float)
    c.add_argument("--stop", type=float)
    c.add_argument("--target", type=float)
    c.add_argument("--qty", type=int, help="your quantity instead of the desk's size (limit breaches are listed)")
    c.add_argument("--choose", action="store_true",
                   help="take a WAIT/SKIP anyway: print the override ticket and log it as your override")
    c.set_defaults(fn=cmd_check)
    for name, fn, h in (("plan", cmd_plan, "today's plan"), ("positions", cmd_positions, "review open positions"),
                        ("intraday", cmd_intraday, "opening-range scan")):
        p = sub.add_parser(name, help=h)
        common(p)
        if name == "intraday":
            p.add_argument("--symbols", help="comma separated; default first 15 of the universe")
        p.set_defaults(fn=fn)
    s = sub.add_parser("settings", help="show or change per-market limits")
    common(s)
    s.add_argument("--capital", type=float)
    s.add_argument("--risk-pct", type=float, help="risk per trade, percent of capital")
    s.add_argument("--max-position-pct", type=float)
    s.add_argument("--daily-loss-pct", type=float)
    s.add_argument("--pdt-guard", choices=["on", "off"])
    s.set_defaults(fn=cmd_settings)
    sb = sub.add_parser("scoreboard", help="paper record of the desk's logged TAKEs")
    sb.add_argument("--market", default="ALL", help="NSE, US or ALL (default ALL)")
    sb.add_argument("--json", action="store_true", help="print JSON instead of text")
    sb.set_defaults(fn=cmd_scoreboard)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        profile = None if args.cmd == "scoreboard" else get_profile(args.market)  # scoreboard accepts ALL
        return args.fn(args, profile)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - data/network failure: clear message, no traceback
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
