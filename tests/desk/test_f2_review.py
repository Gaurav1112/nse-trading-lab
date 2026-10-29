"""F2 final-review fixes in the desk engine (fictional capital only). No network."""
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
from nse_backtest.desk.checks import CheckContext
from nse_backtest.desk.markets import NSE
from nse_backtest.desk.models import Action, Check, Limits, LiveQuote, Mode, ShortEdge, Side, Status
from nse_backtest.desk.nse_lists import BanList
from nse_backtest.desk.service import check_trade

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 29, 10, 32, tzinfo=IST)


def falling_frame(n_days=40, seed=7, end="2026-10-29", last_bars=5):
    """15-minute bars of a slowly falling stock; the last session has a wide opening bar then a breakdown."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end=end, periods=n_days)
    rows, idx, px = [], [], 1000.0
    for k, d in enumerate(days):
        for b in range(last_bars if k == n_days - 1 else 25):
            o = px
            c = o * (1 + rng.normal(-0.0008, 0.003))
            h, lo = max(o, c) * (1 + rng.uniform(0, .002)), min(o, c) * (1 - rng.uniform(0, .002))
            idx.append(d + pd.Timedelta(hours=9, minutes=15 + 15 * b))
            rows.append((o, h, lo, c, 3e5 if b == 0 else 1e5))
            px = c
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex(idx))
    today = df.index[df.index.normalize() == df.index[-1].normalize()]
    o = float(df.loc[today[0], "Open"])
    df.loc[today[0], ["High", "Low", "Close"]] = [o * 1.006, o * 0.992, o * 0.993]
    for i, t in enumerate(today[1:]):
        base = o * (0.992 - 0.0005 * i)
        df.loc[t, ["Open", "High", "Low", "Close"]] = [base, base * 1.001, base * 0.9995, base * 0.9998]
    return df


LIM = Limits(capital=500_000)  # fictional


def short_ctx(**kw):
    base = dict(regime="HOSTILE", governor_can_trade=True, ban=BanList(frozenset(), "ok", "2026-10-29", ""),
                earnings_checked=True, turnover_20d=1e11, today_gap_pct=0.1, fno=frozenset({"FICT"}), fno_as_of=NOW.date(),
                short_edge=ShortEdge.from_trades(250, 0.12))
    base.update(kw)
    return CheckContext(NSE, LIM, NOW, **base)


def _short(entry=None, price=None):
    df = falling_frame()
    sess = df[df.index.normalize() == df.index[-1].normalize()]
    orl = float(sess.iloc[0]["Low"])
    q = LiveQuote(price=(entry if entry is not None else orl) - 0.02, day_open=float(sess.iloc[0]["Open"]),
                  as_of=NOW.replace(minute=31))
    v = check_trade(df, "FICT", NSE, Mode.INTRADAY, LIM, short_ctx(), quote=q, entry=entry, side=Side.SHORT,
                    audit=False, replays=500, used_at=NOW)
    return v, orl


# ---------------------------------------------------------------- I2: a user entry is off the pooled rule

def test_i2_short_at_a_user_entry_is_capped_at_wait():
    _, orl = _short()
    v, _ = _short(entry=round(orl - 2.0, 2))  # probe_user_entry2: two rupees below the OR low
    edge = next(c for c in v.checks if c.name == "short_edge")
    assert edge.status is Status.WARN
    assert edge.reason.endswith("; entry differs from the opening-range low the pooled history tested")
    assert v.action is Action.WAIT and "entry differs from the opening-range low" in v.headline


def test_i2_short_at_the_opening_range_low_stays_proven():
    v, orl = _short()
    assert v.plan.entry == orl
    edge = next(c for c in v.checks if c.name == "short_edge")
    assert edge.status is Status.PASS and v.action is Action.TAKE
    # within half a tick (0.05 on NSE) the entry is the OR low
    v2, _ = _short(entry=orl + 0.02)
    assert next(c for c in v2.checks if c.name == "short_edge").status is Status.PASS


def test_i2_unknown_opening_range_low_is_never_proven():
    from nse_backtest.desk.checks import short_edge_check
    from tests.desk.test_checks import sctx, splan
    c = short_edge_check(sctx(short_edge=ShortEdge.from_trades(250, 0.2), or_low=None), splan())
    assert c.status is Status.WARN and c.reason.endswith("; opening-range low unknown: entry basis not verified")
    c = short_edge_check(sctx(short_edge=ShortEdge.from_trades(250, 0.2), or_low=99.0), splan())
    assert c.status is Status.WARN and c.reason.endswith(
        "; entry differs from the opening-range low the pooled history tested")
    c = short_edge_check(sctx(short_edge=ShortEdge.from_trades(250, 0.2), or_low=100.0), splan())
    assert c.status is Status.PASS


def test_i2_entry_basis_leaves_long_hashes_alone():
    # the OR low lives on the CheckContext (never hashed), not on the TradePlan
    import dataclasses
    from nse_backtest.desk.models import TradePlan
    assert [f.name for f in dataclasses.fields(TradePlan)][-1] == "side"
    assert "or_low" in {f.name for f in dataclasses.fields(CheckContext)}


# ---------------------------------------------------------------- I3: a code bug never looks like a quiet day

def _bug_plan(monkeypatch, exc=TypeError("unsupported operand type(s) for -: 'NoneType' and 'float'")):
    import tests.desk.test_daily as td
    from nse_backtest.desk import daily

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(daily, "check_trade", boom)
    return td._plan()


def test_i3_non_value_errors_are_recorded_as_plan_errors(monkeypatch):
    dp = _bug_plan(monkeypatch)
    assert not dp.take and not dp.watch
    assert dp.errors and len(dp.errors) == len(dp.skipped)
    assert all(why.startswith("TypeError") for _, why in dp.errors)
    import dataclasses
    from nse_backtest.desk.daily import DailyPlan, plan_errors_line
    assert [f.name for f in dataclasses.fields(DailyPlan)][-1] == "errors"
    sym = dp.errors[0][0]
    assert plan_errors_line(dp) == (f"The plan hit errors for {len(dp.errors)} symbol(s): {sym}: "
                                    "TypeError: unsupported operand type(s) for -: 'NoneType' and 'float'")


def test_i3_value_errors_are_unusable_data_not_errors(monkeypatch):
    from nse_backtest.desk.daily import plan_errors_line
    dp = _bug_plan(monkeypatch, ValueError("need at least 20 bars"))
    assert dp.skipped and dp.errors == () and plan_errors_line(dp) is None


def test_i3_cli_prints_the_errors_line_even_when_some_symbols_worked(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    from argparse import Namespace
    from nse_backtest.desk import cli, session
    import tests.desk.test_daily as td
    from nse_backtest.desk.markets import NSE
    good = td._plan()
    plan = dataclasses_replace(good, errors=(("ZZZ", "KeyError: 'Close'"),),
                               skipped=good.skipped + (("ZZZ", "KeyError: 'Close'"),))
    monkeypatch.setattr(session, "run_daily_plan", lambda *a, **k: plan)
    rc = cli.cmd_plan(Namespace(json=False, all=False), NSE)
    out = capsys.readouterr()
    assert rc == 0  # some symbols worked: not PLAN FAILED
    assert "The plan hit errors for 1 symbol(s): ZZZ: KeyError: 'Close'" in out.out
    assert "The plan hit errors for 1 symbol(s)" in out.err


def test_i3_cli_every_symbol_failing_is_still_plan_failed(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    from argparse import Namespace
    from nse_backtest.desk import cli, session
    from nse_backtest.desk.markets import NSE
    dp = _bug_plan(monkeypatch)
    monkeypatch.setattr(session, "run_daily_plan", lambda *a, **k: dp)
    assert cli.cmd_plan(Namespace(json=False, all=False), NSE) == 1
    out = capsys.readouterr().out
    assert "PLAN FAILED for every symbol" in out and "The plan hit errors for" in out


def dataclasses_replace(x, **kw):
    from dataclasses import replace
    return replace(x, **kw)


# ---------------------------------------------------------------- I4: a float tie at 1.5R is not a BLOCK

def _tie_plans(n=100_000, seed=1):
    """probe_rr_tie: auto ORB geometry on the NSE tick grid, long (T1 = OR high + 1.5R) and short (OR low - 1.5R)."""
    from nse_backtest.desk.models import TradePlan
    rng = np.random.default_rng(seed)
    for _ in range(n):
        lo = round(rng.uniform(50, 5000) / 0.05) * 0.05
        hi = lo + round(rng.uniform(0.2, 30) / 0.05) * 0.05
        s = (hi + lo) / 2
        yield TradePlan("X", "NSE", Mode.INTRADAY, hi, s, hi + 1.5 * (hi - s), hi + 3 * (hi - s), 1, 10, hi, "", "")
        yield TradePlan("X", "NSE", Mode.INTRADAY, lo, s, lo - 1.5 * (s - lo), lo - 3 * (s - lo), 1, 10, lo, "", "",
                        side=Side.SHORT)


def test_i4_no_false_reward_risk_blocks_over_100k_auto_plans():
    from nse_backtest.desk.checks import meets_min_rr
    plans = list(_tie_plans())
    raw = sum(not (p.rr_t1 >= 1.5) for p in plans)
    assert raw > 10_000  # the float tie the probe found (~30% of plans) is real
    assert sum(not meets_min_rr(p.rr_t1, 1.5) for p in plans) == 0


def test_i4_tie_passes_and_a_real_shortfall_still_blocks():
    from dataclasses import replace
    from nse_backtest.desk.checks import meets_min_rr, run_checks
    from tests.desk.test_checks import SIZE, ODDS, sctx, splan
    tie = next(p for p in _tie_plans(2000) if p.is_short and not (p.rr_t1 >= 1.5))
    tie = replace(tie, symbol="ABC", last_bar="2026-10-29T09:45:00+05:30")
    c = next(c for c in run_checks(tie, SIZE, ODDS, sctx(or_low=tie.entry), current_price=tie.entry)
             if c.name == "reward_risk")
    assert (c.status, c.reason) == (Status.PASS, "Reward:risk 1:1.50 meets minimum 1:1.50")
    assert not meets_min_rr(1.4999, 1.5) and not meets_min_rr(float("nan"), 1.5)
    low = replace(splan(), target_1=100.0 - 2.0 * 1.49)  # 1.49R: a real shortfall
    c = next(c for c in run_checks(low, SIZE, ODDS, sctx(), current_price=100.0) if c.name == "reward_risk")
    assert c.status is Status.BLOCK and c.reason == "Reward:risk 1:1.49 is below minimum 1:1.50"


# ---------------------------------------------------------------- M1/M4/M8/M10: ranking and groups

def _vv(sym="X", action=Action.WAIT, checks=(), side=Side.LONG, e=0.3, regime="HOSTILE"):
    from dataclasses import replace
    from tests.desk.test_choose import v
    out = v(sym, action, Mode.INTRADAY if side is Side.SHORT else Mode.SWING, e=e,
            checks=checks or (Check("regime", Status.PASS, ""),))
    return replace(out, plan=replace(out.plan, side=side), regime=regime)


def test_m8_unknown_check_names_are_data_never_overridable():
    from nse_backtest.desk.verdict import group_of
    assert group_of("mystery") == "data" and group_of("mystery", Side.SHORT) == "data"


def test_m1_candidate_rank_reads_block_groups_like_the_card():
    from nse_backtest.desk import session
    # an unknown regime (index data failed) puts the regime BLOCK in "data": no longer advice-ranked
    known = _vv(action=Action.SKIP, checks=(Check("regime", Status.BLOCK, "x"),), regime="HOSTILE")
    unknown = _vv(action=Action.SKIP, checks=(Check("regime", Status.BLOCK, "x"),), regime=None)
    assert session.candidate_rank(known)[0] == 2 and session.candidate_rank(unknown)[0] == 3


def test_m10_unknown_regime_is_data_broken_with_its_own_reason_and_no_override(tmp_path):
    from nse_backtest.desk import audit
    from nse_backtest.desk.verdict import REGIME_UNKNOWN, block_groups, data_broken
    v = _vv(action=Action.SKIP, checks=(Check("regime", Status.BLOCK,
                                              "Regime unknown — treated as HOSTILE (swing longs blocked)"),),
            regime=None)
    assert [c.name for c in block_groups(v)["data"]] == ["regime"] and block_groups(v)["market"] == []
    assert data_broken(v) == (REGIME_UNKNOWN,) == ("Market regime unknown (index data unavailable)",)
    with pytest.raises(ValueError, match="re-check"):
        audit.append_override(v, tmp_path / "a.jsonl")  # M2
    assert not (tmp_path / "a.jsonl").exists()
    short = _vv(side=Side.SHORT, action=Action.SKIP, regime=None, checks=(
        Check("regime", Status.WARN, "Market regime unknown: ORB shorts are for HOSTILE days only"),
        Check("short_regime", Status.BLOCK, "ORB shorts are for HOSTILE days only (regime unknown)")))
    assert data_broken(short) == (REGIME_UNKNOWN,)


def test_m2_append_override_refuses_any_data_broken_verdict(tmp_path):
    from nse_backtest.desk import audit
    v = _vv(action=Action.SKIP, checks=(Check("freshness", Status.BLOCK, "Data is stale"),))
    with pytest.raises(ValueError, match="data is broken"):
        audit.append_override(v, tmp_path / "a.jsonl")


def test_m4_unproven_shorts_rank_after_proven_peers_within_wait():
    from nse_backtest.desk import session
    proven = Check("short_edge", Status.PASS, "edge proven")
    unproven = Check("short_edge", Status.WARN, "edge not proven")
    long_w = _vv("L", e=0.2)
    short_ok = _vv("SP", side=Side.SHORT, e=0.3, checks=(proven,))
    short_un = _vv("SU", side=Side.SHORT, e=0.9, checks=(unproven,))  # its 1x-slippage Exp R is the highest
    order = [x.plan.symbol for x in sorted([short_un, long_w, short_ok], key=session.candidate_rank)]
    assert order == ["SP", "L", "SU"]


def test_m4_short_expected_value_is_labelled_with_its_slippage_basis():
    from nse_backtest.desk.tradecard import ev_basis_note
    assert ev_basis_note(_vv(side=Side.SHORT)) == "at 1× slippage (the edge gate uses 2×)"
    assert ev_basis_note(_vv()) == ""


# ---------------------------------------------------------------- M5: F&O list freshness

def test_m5_fetch_returns_the_list_and_its_date(tmp_path):
    from datetime import date
    from nse_backtest.desk.nse_lists import fetch_fno_underlyings
    from tests.desk.test_nse_lists import LOTS

    def boom(u):
        raise OSError("down")
    syms, as_of = fetch_fno_underlyings(tmp_path, date(2026, 10, 29), fetch=lambda u: LOTS)
    assert "ALPHA" in syms and as_of == date(2026, 10, 29)
    syms, as_of = fetch_fno_underlyings(tmp_path, date(2026, 11, 2), fetch=boom)
    assert "ALPHA" in syms and as_of == date(2026, 10, 29)  # the cached copy says how old it is
    assert fetch_fno_underlyings(tmp_path, date(2026, 11, 6), fetch=boom) == (None, None)  # over 7 days


def test_m5_short_fno_warns_on_a_cached_list_and_blocks_an_old_one():
    from datetime import date
    from nse_backtest.desk.checks import run_checks
    from tests.desk.test_checks import ODDS, SIZE, sby, sctx, splan
    today = NOW.date()
    c = sby(run_checks(splan(), SIZE, ODDS, sctx(fno_as_of=today), current_price=100.0))["short_fno"]
    assert c.status is Status.PASS
    c = sby(run_checks(splan(), SIZE, ODDS, sctx(fno_as_of=date(2026, 10, 27)), current_price=100.0))["short_fno"]
    assert (c.status, c.reason) == (Status.WARN, "F&O list from 2026-10-27 (live fetch failed)")
    c = sby(run_checks(splan(), SIZE, ODDS, sctx(fno_as_of=date(2026, 10, 20)), current_price=100.0))["short_fno"]
    assert c.status is Status.BLOCK and "older than 7 days" in c.reason
    c = sby(run_checks(splan(), SIZE, ODDS, sctx(fno_as_of=None), current_price=100.0))["short_fno"]
    assert (c.status, c.reason) == (Status.WARN, "F&O list date unknown — verify on NSE")


def test_m11_loaders_fno_uses_the_market_date(monkeypatch, tmp_path):
    from datetime import date, timezone
    from nse_backtest.desk import loaders, nse_lists
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    asked = []
    monkeypatch.setattr(nse_lists, "fetch_fno_underlyings", lambda d, today: asked.append(today) or (None, None))
    # 20:00 UTC on Oct 29 is 01:30 IST on Oct 30
    loaders.fno_underlyings(NSE, datetime(2026, 10, 29, 20, 0, tzinfo=timezone.utc))
    assert asked == [date(2026, 10, 30)]


def test_m11_fo_mktlots_parses_quoted_csv_cells():
    from nse_backtest.desk.nse_lists import parse_fno_underlyings
    text = 'UNDERLYING,SYMBOL,OCT-26\n"Nifty, 50",NIFTY,75\nAlpha Ltd,"ALPHA",500\n'
    assert parse_fno_underlyings(text) == frozenset({"NIFTY", "ALPHA"})


# ---------------------------------------------------------------- M6/M11: pooled coverage floor, cache digest

def covered(edge, pooled=40, eligible=40):
    """``edge`` with its coverage: ``pooled`` of ``eligible`` symbols replayed (fictional symbols and dates)."""
    from dataclasses import replace
    syms = tuple((f"S{i:02d}", "2026-10-29") for i in range(pooled))
    return replace(ShortEdge.from_trades(edge.n, edge.expectancy_r, pooled=pooled, eligible=eligible),
                   skipped=edge.skipped, skip_reason=edge.skip_reason, pooled_symbols=syms)


def test_m6_proven_needs_80_percent_of_the_eligible_symbols_pooled():
    from nse_backtest.desk.checks import short_edge_check
    from tests.desk.test_checks import sctx, splan
    assert ShortEdge.from_trades(250, 0.2, pooled=32, eligible=40).proven  # exactly 80%
    thin = ShortEdge.from_trades(250, 0.2, pooled=31, eligible=40)
    assert not thin.proven
    forged = covered(ShortEdge.from_trades(250, 0.2), pooled=31, eligible=40)
    from dataclasses import replace
    c = short_edge_check(sctx(short_edge=replace(forged, proven=True)), splan())
    assert c.status is Status.WARN and "pooled 31 of 40 symbols (needs 80%)" in c.reason


def test_m6_pooled_short_edge_counts_coverage_and_the_line_says_it():
    from nse_backtest.desk.shortcheck import edge_line, pooled_short_edge
    from tests.desk.test_shortcheck import all_hostile, frame
    a, b = frame(150), frame(150)
    e = pooled_short_edge(NSE, {"AAA": a, "BBB": b, "NONE": None}, all_hostile(a, b), eligible=5)
    assert (e.pooled, e.eligible) == (2, 5) and not e.proven  # 2 of 5: under 80% however many trades
    assert [s for s, _ in e.pooled_symbols] == ["AAA", "BBB"]
    assert "pooled 2 of 5 symbols; 3 skipped" in edge_line(e)


def test_m11_cache_round_trip_keeps_coverage_and_rejects_a_tampered_list(tmp_path):
    import json
    from datetime import date
    from nse_backtest.desk.shortcheck import load_edge, save_edge
    p, day = tmp_path / "c.json", date(2026, 10, 29)
    e = covered(ShortEdge.from_trades(250, 0.1), pooled=36, eligible=40)
    save_edge(p, NSE, day, e, symbols=36)
    got, why = load_edge(p, NSE, day)
    assert why == "" and got.proven and (got.pooled, got.eligible) == (36, 40)
    good = json.loads(p.read_text())
    assert len(good["digest"]) == 64
    for patch in ({"pooled_symbols": good["pooled_symbols"][:-1]},  # a symbol dropped
                  {"pooled_symbols": [["XX", "2026-10-29"]] + good["pooled_symbols"][1:]},  # a symbol swapped
                  {"n": 260, "proven": True},  # the numbers edited
                  {"eligible": 36},  # the coverage edited
                  {"digest": "0" * 64}):
        p.write_text(json.dumps({**good, **patch}))
        got, why = load_edge(p, NSE, day)
        assert got is None and "unreadable" in why, patch
    old = {k: v for k, v in good.items() if k not in ("digest", "pooled_symbols", "pooled", "eligible")}
    p.write_text(json.dumps({**old, "version": 1}))
    assert load_edge(p, NSE, day)[0] is None  # an old-format cache is recomputed, never trusted
