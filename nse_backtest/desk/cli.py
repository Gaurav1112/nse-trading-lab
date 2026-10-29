"""Command line: python -m nse_backtest.desk {check,plan,positions,intraday,settings}. Places nothing."""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
from dataclasses import replace
from datetime import date, datetime
from enum import Enum

from nse_backtest import tape_monitor

from . import loaders
from .daily import daily_plan, market_stats
from .intraday import orb_scan
from .markets import get_profile, universe
from .models import Action, DeskVerdict, Limits, Mode
from .positions import review_positions
from .service import check_trade

FOOTER = "Not financial advice. Nothing was placed. Paper-trade until your record matches the odds."
PLAN_UNIVERSE_LIMIT: int | None = None  # None = whole universe


# ---------------------------------------------------------------- output helpers

def to_jsonable(x):
    """Dataclasses, enums, dates, numpy scalars and non-finite floats to JSON-safe values."""
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return {f.name: to_jsonable(getattr(x, f.name)) for f in dataclasses.fields(x)}
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


def format_verdict(v: DeskVerdict, profile) -> list[str]:
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
            lines.append(f"Noise range (shuffled data): {_r(o.noise_expectancy_min)} to {_r(o.noise_expectancy_max)}")
    lines += ["", "Checks:"]
    lines += [f"  {c.status.value} {c.name} — {c.reason}" for c in v.checks]
    if v.ticket:
        lines += ["", "Ticket (type it in yourself):"] + [f"  {t}" for t in v.ticket]
    return lines


def _finish(lines: list[str]) -> int:
    print("\n".join(lines + ["", FOOTER]))
    return 0


def _limits(profile, as_json: bool = False):
    """(limits, warning lines). A corrupt settings file falls back to defaults, loudly."""
    limits, fell_back = loaders.load_limits_with_status(profile)
    if not fell_back:
        return limits, []
    msg = f"WARNING: settings file unreadable — using default capital {profile.money(limits.capital)}"
    if as_json:
        print(msg, file=sys.stderr)
        return limits, []
    return limits, [msg, ""]


# ---------------------------------------------------------------- data assembly

def _regime_of(regime_df) -> str | None:
    try:
        tape = tape_monitor.assess_tape(regime_df)
        return tape.regime if tape else None
    except Exception as e:  # noqa: BLE001 - unknown regime is handled fail-closed downstream
        print(f"warning: regime unavailable: {e}", file=sys.stderr)
        return None


def _regime(profile) -> str | None:
    return _regime_of(loaders.load_regime(profile))


def _ctx_for(profile, limits, symbol_df=None, regime=None):
    return loaders.build_context(profile, limits, symbol_df=symbol_df, regime=regime)


def _frames(profile, symbols) -> dict:
    out = {}
    for sym in symbols:
        try:
            out[sym] = loaders.load_daily(profile, sym)
        except Exception as e:  # noqa: BLE001 - reported as unavailable, never dropped silently
            print(f"warning: no data for {sym}: {e}", file=sys.stderr)
            out[sym] = None
    return out


# ---------------------------------------------------------------- commands

def cmd_check(a, profile) -> int:
    sym = a.symbol.strip().upper()
    limits, warn = _limits(profile, a.json)
    mode = Mode(a.mode)
    daily = loaders.load_daily(profile, sym)
    df = loaders.load_intraday(profile, sym) if mode is Mode.INTRADAY else daily
    ctx = _ctx_for(profile, limits, daily, _regime(profile))
    nxt, checked = loaders.next_earnings(profile, sym)
    ctx = replace(ctx, next_earnings=nxt, earnings_checked=checked)
    v = check_trade(df, sym, profile, mode, limits, ctx, entry=a.entry, stop=a.stop, target=a.target)
    if a.json:
        _emit_json(v)
        return 0
    return _finish(warn + format_verdict(v, profile))


def _format_row(v: DeskVerdict, profile, *, why: bool = False) -> str:
    e = v.odds.mc_expectancy_r if v.odds is not None else None
    row = f"  {v.headline}  [exp {_r(e)}]"
    if why:
        blocks = [c.name for c in v.blocks()]
        warns = [c.name for c in v.checks if c.status.value == "WARN"]
        if blocks or warns:
            row += f"\n    blocked by: {', '.join(blocks) or 'none'}; warnings: {', '.join(warns) or 'none'}"
    return row


def cmd_plan(a, profile) -> int:
    limits, warn = _limits(profile, a.json)
    syms = universe(profile)[:PLAN_UNIVERSE_LIMIT]
    frames = _frames(profile, syms)
    regime_df = loaders.load_regime(profile)
    ctx = loaders.build_context(profile, limits, regime=_regime_of(regime_df))
    plan = daily_plan(profile, frames, regime_df, limits, ctx,
                      earnings_lookup=lambda s: loaders.next_earnings(profile, s))
    if a.json:
        _emit_json(plan)
        return 0
    lines = warn + [f"Daily plan {profile.code} — regime {plan.regime or 'UNKNOWN'}: {plan.regime_text}"]
    if plan.flip_conditions:
        lines += ["Regime flips if:"] + [f"  - {c}" for c in plan.flip_conditions]
    lines += ["", "TAKE:"] + ([_format_row(v, profile) for v in plan.take] or ["  none today — no trade is a valid answer"])
    lines += ["", "WATCH:"] + ([_format_row(v, profile, why=True) for v in plan.watch] or ["  none"])
    for v in plan.take:
        lines += [""] + format_verdict(v, profile)
    if plan.unavailable:
        lines += ["", "No data: " + ", ".join(plan.unavailable)]
    lines += ["", f"Skipped {len(plan.skipped)} symbols."]
    return _finish(lines)


def cmd_positions(a, profile) -> int:
    positions = loaders.market_positions(profile)
    frames = _frames(profile, sorted({str(p["symbol"]) for p in positions if p.get("symbol") and not p.get("closed")}))
    actions = review_positions(positions, {k: v for k, v in frames.items() if v is not None}, profile)
    if a.json:
        _emit_json(actions)
        return 0
    lines = [f"Open positions {profile.code}: {len(actions)}"]
    for x in actions:
        px = "n/a" if x.current_price is None else profile.money(x.current_price)
        r = "n/a" if x.r_so_far is None else f"{x.r_so_far:+.2f}R"
        sug = "" if x.suggested_stop is None else f"  suggested stop {profile.money(x.suggested_stop)}"
        lines.append(f"  {x.action:<16} {x.symbol}  now {px}  entry {profile.money(x.entry)}  "
                     f"stop {profile.money(x.stop)}{sug}  {r}  — {x.reason}")
    return _finish(lines)


def cmd_intraday(a, profile) -> int:
    limits, warn = _limits(profile, a.json)
    syms = ([s.strip().upper() for s in a.symbols.split(",") if s.strip()]
            if a.symbols else universe(profile)[:15])
    frames15, load_failures, per_symbol = {}, [], {}
    ctx = _ctx_for(profile, limits, regime=_regime(profile))
    for s in syms:
        try:
            frames15[s] = loaders.load_intraday(profile, s)
        except Exception as e:  # noqa: BLE001
            load_failures.append((s, str(e)))
            continue
        try:  # daily bars give the liquidity (turnover) and gap checks something to read
            turnover, gap = market_stats(loaders.load_daily(profile, s))
            per_symbol[s] = replace(ctx, turnover_20d=turnover, today_gap_pct=gap)
        except Exception as e:  # noqa: BLE001 - liquidity then stays unknown, which the desk WARNs on
            print(f"warning: no daily data for {s}: {e}", file=sys.stderr)
    verdicts, failures = orb_scan(profile, frames15, limits, ctx, ctx_by_symbol=per_symbol)
    failures = load_failures + list(failures)
    if a.json:
        _emit_json({"verdicts": verdicts, "failures": failures})
        return 0
    lines = warn + [f"Intraday opening-range scan {profile.code} ({len(syms)} symbols)"]
    for v in verdicts:
        if v.action is Action.SKIP:
            lines.append(_format_row(v, profile))
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


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m nse_backtest.desk",
                                 description="Trade desk: sized TAKE / WAIT / SKIP decisions for NSE and US. Places nothing.")
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
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args, get_profile(args.market))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - data/network failure: clear message, no traceback
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
