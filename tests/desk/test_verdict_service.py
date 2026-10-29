import json
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd, pytest
from nse_backtest.desk.verdict import decide, ticket_lines
from nse_backtest.desk.costs import round_trip_cost
from nse_backtest.desk.service import check_trade
from nse_backtest.desk.checks import CheckContext
from nse_backtest.desk.models import Action, Check, Limits, Mode, Sizing, Status, TradePlan
from nse_backtest.desk.markets import NSE, US
from nse_backtest.desk.nse_lists import BanList

NOW = datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
P = TradePlan("ABC", "NSE", Mode.SWING, 100, 98, 103, 106, 2, 15, 100, "2026-10-28T00:00:00", "user")
S = Sizing(250, "position cap", 640.0, 25_000.0, 140.0, False, "250 shares")
OK = (Check("regime", Status.PASS, ""),)
PI = replace(P, mode=Mode.INTRADAY, entry_limit=100.5)


def test_take_ticket_nse_swing():
    v = decide(P, S, None, OK, NSE, 100.0, NOW)
    assert v.action is Action.TAKE and v.ticket == ("BUY 250 ABC CNC LIMIT 100.00", "GTT OCO ABC: stop-loss 98.00 / target 103.00")
    # F1 A1: the headline's loss is sizing.loss_at_stop (the card's max loss), never a hand-set risk_amount
    loss = 250 * 2 + round_trip_cost(NSE, Mode.SWING, 100, 98, 250)
    assert v.headline == f"TAKE ABC — BUY 250 @ ₹100.00, loss at stop ≈ {NSE.money(loss)} (a gap can lose more)"
    assert len(v.input_hash) == 64


def test_block_means_skip():
    v = decide(P, S, None, OK + (Check("regime", Status.BLOCK, "x"), Check("gap", Status.WARN, "y")), NSE, 100.0, NOW)
    assert v.action is Action.SKIP and v.ticket == () and v.headline == "SKIP ABC — blocked by: regime"


def test_wait_below_trigger_and_after_runaway():
    below = decide(P, S, None, OK, NSE, 99.0, NOW)
    assert below.action is Action.WAIT and below.trigger == 100 and below.ticket[0] == "IF TRIGGERED:"
    assert decide(P, S, None, OK, NSE, 101.5, NOW).action is Action.WAIT


def test_us_and_intraday_tickets():
    us = ticket_lines(replace(P, market="US"), S, US)
    assert us == ("BUY 250 ABC LIMIT 100.00 — bracket: stop-loss 98.00, take-profit 103.00, GTC",)
    mis = ticket_lines(PI, S, NSE)  # C1: the intraday entry is a stop-limit (SL) order
    # F1 C2: cancel at the end of the last entry bar (14:30 + 15 min; US 15:00 + 15 min)
    assert mis == ("BUY 250 ABC MIS SL trigger 100.00 limit 100.50", "Cancel the entry if not filled by 14:45 IST",
                   "SL-M SELL 250 ABC trigger 98.00", "SELL 250 ABC LIMIT 103.00", "Square off by 15:15 IST")
    us_mis = ticket_lines(replace(PI, market="US"), S, US)
    assert us_mis == ("BUY 250 ABC STOP-LIMIT stop 100.00 limit 100.50 — bracket: stop-loss 98.00, "
                      "take-profit 103.00, DAY", "Cancel the entry if not filled by 15:15 ET", "Flat by 15:55 ET")
    # a plan built without a limit price gets the same formula (entry 100, stop 98, NSE tick)
    assert ticket_lines(replace(P, mode=Mode.INTRADAY), S, NSE)[0] == "BUY 250 ABC MIS SL trigger 100.00 limit 100.50"


def test_intraday_take_needs_price_at_or_below_the_stop_limit():
    assert decide(PI, S, None, OK, NSE, 100.5, NOW).action is Action.TAKE
    v = decide(PI, S, None, OK, NSE, 100.55, NOW)
    assert v.action is Action.WAIT and v.trigger == 100 and v.ticket[0] == "IF TRIGGERED:"
    assert v.headline == ("WAIT ABC — Price ₹100.55 is above the stop-limit's limit ₹100.50 — "
                          "the order would not fill")
    assert decide(P, S, None, OK, NSE, 100.55, NOW).action is Action.TAKE  # swing keeps the 1% band


def test_hash_is_stable_and_input_sensitive():
    a = decide(P, S, None, OK, NSE, 100.0, NOW).input_hash
    assert a == decide(P, S, None, OK, NSE, 100.0, NOW).input_hash
    assert a != decide(replace(P, stop=97), S, None, OK, NSE, 100.0, NOW).input_hash


def rising(n=400):
    idx = pd.bdate_range(end="2026-10-28", periods=n)
    close = 100 * 1.004 ** np.arange(1, n + 1); prev = close / 1.004
    return pd.DataFrame({"Open": prev, "High": prev * 1.012, "Low": prev * 0.995, "Close": close, "Volume": 5e6}, index=idx)


def test_check_trade_end_to_end_writes_audit(tmp_path):
    c = CheckContext(profile=NSE, limits=Limits(capital=100_000), now=NOW, regime="TRENDING", governor_can_trade=True,
                     ban=BanList(frozenset(), "ok", None, ""), earnings_checked=True, turnover_20d=1e10, today_gap_pct=0.1)
    path = tmp_path / "audit.jsonl"
    v = check_trade(rising(), "ABC", NSE, Mode.SWING, Limits(capital=100_000), c, replays=1000, audit_path=path)
    assert v.action in (Action.TAKE, Action.WAIT, Action.SKIP) and v.odds is not None
    assert len(v.checks) == 17
    line = json.loads(path.read_text().strip().splitlines()[-1])
    assert line["symbol"] == "ABC" and line["input_hash"] == v.input_hash


def test_check_trade_hostile_is_skip(tmp_path):
    c = CheckContext(profile=NSE, limits=Limits(capital=100_000), now=NOW, regime="HOSTILE")
    v = check_trade(rising(), "ABC", NSE, Mode.SWING, Limits(capital=100_000), c, replays=500, robustness=False, audit_path=tmp_path / "a.jsonl")
    assert v.action is Action.SKIP and "regime" in v.headline


# ---- Final fix wave A1: a 3-bar intraday session (no odds) used to be TAKE; now SKIP ----
def test_a1_three_bar_session_without_odds_is_skip(tmp_path, monkeypatch):
    day = pd.Timestamp("2026-10-28")
    idx = pd.DatetimeIndex([day + pd.Timedelta(hours=9, minutes=15 + 15 * i) for i in range(3)])
    df = pd.DataFrame({"Open": [100, 100.5, 101.2], "High": [101, 101.6, 102.0], "Low": [99.5, 100.2, 101.0],
                       "Close": [100.5, 101.2, 101.05], "Volume": [1e6] * 3}, index=idx)
    now = datetime(2026, 10, 28, 9, 50, tzinfo=ZoneInfo("Asia/Kolkata"))
    c = CheckContext(profile=NSE, limits=Limits(capital=100_000), now=now, regime="TRENDING", governor_can_trade=True,
                     ban=BanList(frozenset(), "ok", "2026-10-28", ""), earnings_checked=True,
                     turnover_20d=1e9, today_gap_pct=0.2)
    from nse_backtest.desk import service
    logged = []
    monkeypatch.setattr(service.log, "warning", lambda msg, *a: logged.append(msg % a))
    v = check_trade(df, "X", NSE, Mode.INTRADAY, Limits(capital=100_000), c, replays=500, audit=False,
                    entry=101.0, target=102.5)
    assert v.odds is None and v.action is Action.SKIP and v.ticket == ()
    assert "monte_carlo" in [b.name for b in v.blocks()]
    assert any("odds could not be computed" in m and "Monte Carlo" in m for m in logged)


# ---- Final fix wave A4/A5: price unavailable, live quote drives the verdict ----
@pytest.mark.parametrize("px", [float("nan"), float("inf"), 0.0, -1.0])
def test_a4_bad_current_price_is_skip(px):
    v = decide(P, S, None, OK, NSE, px, NOW)
    assert v.action is Action.SKIP and v.ticket == () and v.headline == "SKIP ABC — current price unavailable"


def _ctx_ok(**kw):
    base = dict(profile=NSE, limits=Limits(capital=100_000), now=NOW, regime="TRENDING", governor_can_trade=True,
                ban=BanList(frozenset(), "ok", None, ""), earnings_checked=True, turnover_20d=1e10, today_gap_pct=0.1)
    base.update(kw)
    return CheckContext(**base)


def _quote(price, minutes_old=1):
    from datetime import timedelta
    from nse_backtest.desk.models import LiveQuote
    return LiveQuote(price=price, day_open=price, as_of=NOW - timedelta(minutes=minutes_old))


def test_a5_check_trade_uses_live_quote_and_marks_context_live():
    df = rising()
    last = float(df["Close"].iloc[-1])
    v = check_trade(df, "ABC", NSE, Mode.SWING, Limits(capital=100_000), _ctx_ok(), replays=300,
                    robustness=False, audit=False, quote=_quote(last))
    lp = [c for c in v.checks if c.name == "live_price"][0]
    assert v.current_price == last and lp.status is Status.PASS
    v0 = check_trade(df, "ABC", NSE, Mode.SWING, Limits(capital=100_000), _ctx_ok(price_is_live=True), replays=300,
                     robustness=False, audit=False)
    assert [c for c in v0.checks if c.name == "live_price"][0].status is Status.WARN  # no quote -> never "live"


def test_a5_wait_branches_fire_with_live_quote():
    df = rising()
    last = float(df["Close"].iloc[-1])
    kw = dict(replays=300, robustness=False, audit=False)
    below = check_trade(df, "ABC", NSE, Mode.SWING, Limits(capital=100_000), _ctx_ok(), quote=_quote(last * 0.99), **kw)
    ran = check_trade(df, "ABC", NSE, Mode.SWING, Limits(capital=100_000), _ctx_ok(), quote=_quote(last * 1.02), **kw)
    for v in (below, ran):
        assert not v.blocks(), [c for c in v.blocks()]
    assert below.action is Action.WAIT and "buy only above" in below.headline
    assert ran.action is Action.WAIT and "ran past entry" in ran.headline


def test_a5_live_price_below_stop_blocks_trade():
    df = rising()
    v = check_trade(df, "ABC", NSE, Mode.SWING, Limits(capital=100_000), _ctx_ok(), replays=300,
                    robustness=False, audit=False, quote=_quote(1.0))
    assert v.action is Action.SKIP and "live_price" in [c.name for c in v.blocks()]


# ---------------------------------------------------------------- F1 C: ticket correctness

from datetime import date  # noqa: E402

UPI = replace(PI, market="US")


def test_c1_nse_price_at_or_above_trigger_is_a_plain_limit_at_the_limit_price():
    """Kite rejects an SL buy whose trigger is below the last traded price."""
    above = ticket_lines(PI, S, NSE, current_price=100.2, day=date(2026, 10, 29))
    assert above[0] == "BUY 250 ABC MIS LIMIT 100.50"
    assert ticket_lines(PI, S, NSE, current_price=100.0, day=date(2026, 10, 29))[0] == "BUY 250 ABC MIS LIMIT 100.50"
    below = ticket_lines(PI, S, NSE, current_price=99.9, day=date(2026, 10, 29))
    assert below[0] == "BUY 250 ABC MIS SL trigger 100.00 limit 100.50"
    assert above[1:] == below[1:]


def test_c1_us_price_at_or_above_trigger_is_a_limit_bracket():
    above = ticket_lines(UPI, S, US, current_price=100.3, day=date(2026, 10, 29))
    assert above[0] == "BUY 250 ABC LIMIT 100.50 — bracket: stop-loss 98.00, take-profit 103.00, DAY"
    below = ticket_lines(UPI, S, US, current_price=99.0, day=date(2026, 10, 29))
    assert below[0] == ("BUY 250 ABC STOP-LIMIT stop 100.00 limit 100.50 — bracket: stop-loss 98.00, "
                        "take-profit 103.00, DAY")


def test_c1_decide_and_card_and_override_use_the_current_price():
    from nse_backtest.desk.tradecard import build_card
    take = decide(PI, S, None, OK, NSE, 100.2, NOW)
    assert take.action is Action.TAKE and take.ticket[0] == "BUY 250 ABC MIS LIMIT 100.50"
    assert build_card(take, NSE, Limits(capital=1_000_000)).ticket[0] == "BUY 250 ABC MIS LIMIT 100.50"
    wait = decide(PI, S, None, OK, NSE, 99.0, NOW)  # below the trigger: the conditional ticket is the stop-limit
    assert wait.ticket[1] == "BUY 250 ABC MIS SL trigger 100.00 limit 100.50"
    skip = replace(take, action=Action.SKIP)
    over = build_card(skip, NSE, Limits(capital=1_000_000), override_confirmed=True)
    assert over.ticket[0] == "BUY 250 ABC MIS LIMIT 100.50"
    assert build_card(replace(skip, current_price=99.5), NSE, Limits(capital=1_000_000),
                      override_confirmed=True).ticket[0] == "BUY 250 ABC MIS SL trigger 100.00 limit 100.50"


def test_c2_cancel_line_is_the_end_of_the_last_entry_bar():
    nse = ticket_lines(PI, S, NSE, current_price=99.0, day=date(2026, 10, 29))
    assert nse[1] == "Cancel the entry if not filled by 14:45 IST" and nse[-1] == "Square off by 15:15 IST"
    us = ticket_lines(UPI, S, US, current_price=99.0, day=date(2026, 10, 29))
    assert us[1:] == ("Cancel the entry if not filled by 15:15 ET", "Flat by 15:55 ET")


def test_c2_early_close_pulls_cancel_and_flat_by():
    us = ticket_lines(UPI, S, US, current_price=99.0, day=date(2026, 11, 27))  # 13:00 close -> flat by 12:45
    assert us[1:] == ("Cancel the entry if not filled by 12:45 ET", "Flat by 12:45 ET")
    v = decide(UPI, S, None, OK, US, 99.0, datetime(2026, 11, 27, 10, 0, tzinfo=ZoneInfo("America/New_York")))
    assert v.ticket[-2:] == ("Cancel the entry if not filled by 12:45 ET", "Flat by 12:45 ET")


@pytest.mark.parametrize("market", ["NSE", "US"])
@pytest.mark.parametrize("volume,spread", [(1e9, 0.0003), (1e3, 0.0012)])  # thick / thin turnover frames
def test_i1_check_trade_card_and_headline_use_the_frames_spread(market, volume, spread, tmp_path):
    from nse_backtest.desk.tradecard import build_card
    prof = US if market == "US" else NSE
    df = rising().assign(Volume=volume)
    lim = Limits(capital=1_000_000, max_position_pct=100, cash_buffer_pct=0)
    v = check_trade(df, "ABC", prof, Mode.SWING, lim, _ctx_ok(profile=prof, limits=lim), replays=300,
                    robustness=False, audit=False, entry=float(df["Close"].iloc[-1]),
                    stop=float(df["Close"].iloc[-1]) * 0.95)
    assert v.sizing.spread_per_side == spread
    c = build_card(v, prof, lim)
    assert c.max_loss == pytest.approx(v.sizing.risk_amount) and c.max_loss <= lim.capital / 100
    if v.action is Action.TAKE:
        assert prof.money(v.sizing.risk_amount) in v.headline


def test_r8_check_trade_marks_whether_the_price_was_a_live_quote(tmp_path):
    df = rising()
    lim = Limits(capital=100_000)
    v = check_trade(df, "ABC", NSE, Mode.SWING, lim, _ctx_ok(), replays=200, robustness=False, audit=False)
    assert v.quote_live is False  # no quote: the plan's last close
    q = check_trade(df, "ABC", NSE, Mode.SWING, lim, _ctx_ok(price_is_live=True), replays=200, robustness=False,
                    audit=False, quote=_quote(float(df["Close"].iloc[-1])), used_at=NOW)
    assert q.quote_live is True


@pytest.mark.parametrize("minutes_old,live", [(1, True), (5, True), (6, False), (60 * 20, False)])
def test_r9_quote_live_needs_a_fresh_same_session_quote(minutes_old, live):
    """F1 r9: a quote more than 5 minutes old when it is used, or from another session, is not live."""
    from datetime import timedelta
    df = rising()
    q = check_trade(df, "ABC", NSE, Mode.SWING, Limits(capital=100_000), _ctx_ok(price_is_live=True), replays=200,
                    robustness=False, audit=False, quote=_quote(float(df["Close"].iloc[-1]), minutes_old=minutes_old),
                    used_at=NOW)
    assert q.quote_live is live
    assert q.quote_as_of == (NOW - timedelta(minutes=minutes_old)).isoformat()  # the quote's own time, logged


def test_r9_check_trade_without_a_quote_logs_no_quote_time():
    v = check_trade(rising(), "ABC", NSE, Mode.SWING, Limits(capital=100_000), _ctx_ok(), replays=200,
                    robustness=False, audit=False)
    assert v.quote_live is False and v.quote_as_of is None


# ---------------------------------------------------------------- H3: advice vs blocks

import re as _re  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

from nse_backtest.desk import checks as _checks  # noqa: E402
from nse_backtest.desk import verdict as _verdict  # noqa: E402
from nse_backtest.desk.checks import run_checks  # noqa: E402
from nse_backtest.desk.models import DeskVerdict, Odds  # noqa: E402


def _h3_ctx(regime, **kw):
    base = dict(regime=regime, governor_can_trade=True, ban=BanList(frozenset(), "ok", "2026-10-29", ""),
                earnings_checked=True, turnover_20d=1e12, today_gap_pct=0.5)
    base.update(kw)
    return CheckContext(NSE, Limits(capital=1_000_000), NOW, **base)


def _h3_verdict(regime="HOSTILE", profile=NSE, **kw):
    """A verdict as check_trade builds it: decide over run_checks, carrying the regime value it was checked in."""
    plan = replace(P, market=profile.code)
    ctx = replace(_h3_ctx(regime, **kw), profile=profile)
    cs = run_checks(plan, S, None, ctx, 100.0)
    return replace(decide(plan, S, None, cs, profile, 100.0, NOW), regime=regime)


def test_h3_input_hash_of_an_unchanged_long_verdict_is_pinned():
    """Groups are derived, never stored on Check: a long verdict's input hash is the same as before H3."""
    assert _h3_verdict("HOSTILE").input_hash == "de3294528de29ced8832c6b21360aecb03e4593e33de72371ae51a7f04b87bfe"
    assert _h3_verdict("TRENDING").input_hash == "aa7b9f297ae23ea3b866bd4c037e6936ac049041d2cfe8f494bc9992f426e492"


def _emitted_names() -> set[str]:
    """Every check name run_checks can emit: the literals in checks.py plus a live run per market and mode."""
    src = (_Path(_checks.__file__)).read_text()
    names = set(_re.findall(r'_ck\(\s*"([a-z_]+)"', src))
    odds = Odds(500, 19, .4, .4, .2, .3, 30, 50.0, .3, (.3,) * 30, 1, 3, 6, .1, .2, .5, .1)
    for mode in (Mode.SWING, Mode.INTRADAY):
        for prof in (NSE, US):
            p = replace(P, mode=mode, market=prof.code)
            ctx = CheckContext(prof, Limits(capital=1_000_000), NOW)
            for o in (None, odds):
                names |= {c.name for c in run_checks(p, S, o, ctx, 100.0)}
    return names


def test_h3_every_check_name_is_mapped_to_exactly_one_group():
    names = _emitted_names()
    assert len(names) == 17
    assert set(_verdict.CHECK_GROUPS) == names  # mapped explicitly, nothing left to the "limits" default
    assert set(_verdict.CHECK_GROUPS.values()) == {"market", "odds", "data", "limits"}
    assert _verdict.CHECK_GROUPS["regime"] == "market"
    assert {n for n, g in _verdict.CHECK_GROUPS.items() if g == "data"} == {"freshness", "live_price", "gap"}
    for n in ("monte_carlo", "setup_history", "robustness"):
        assert _verdict.CHECK_GROUPS[n] == "odds"
    for n in ("size", "risk_governor", "daily_loss", "pdt", "ban_list", "stoploss_streak", "liquidity",
              "entry_window", "reward_risk"):
        assert _verdict.CHECK_GROUPS[n] == "limits"


def test_h3_block_groups_splits_non_pass_checks_and_unknown_names_go_to_limits():
    checks = (Check("regime", Status.BLOCK, "r"), Check("monte_carlo", Status.BLOCK, "m"),
              Check("freshness", Status.BLOCK, "f"), Check("liquidity", Status.WARN, "l"),
              Check("size", Status.PASS, "ok"), Check("window", Status.BLOCK, "w"))
    v = decide(P, S, None, checks, NSE, 100.0, NOW)
    g = _verdict.block_groups(v)
    assert list(g) == ["market", "odds", "data", "limits"]
    assert [c.name for c in g["market"]] == ["regime"] and [c.name for c in g["odds"]] == ["monte_carlo"]
    assert [c.name for c in g["data"]] == ["freshness"]
    assert [c.name for c in g["limits"]] == ["liquidity", "window"]  # PASS left out; unknown -> limits
    assert _verdict.data_broken(v) == ("f",)
    assert _verdict.data_broken(decide(P, S, None, (Check("gap", Status.WARN, "g"),), NSE, 100.0, NOW)) == ()


def test_h3_regime_check_text_and_override_note_share_one_held_out_figure():
    v = _h3_verdict("HOSTILE")
    reg = next(c for c in v.checks if c.name == "regime")
    assert reg.reason == "Market regime HOSTILE: swing longs historically lose here (held-out 2026: −1.61%/trade)"
    assert _checks.REGIME_HELD_OUT_PCT == {("NSE", "HOSTILE"): -1.61}
    x = NSE.money(0.0161 * S.notional)
    assert _verdict.regime_override_note(v, NSE) == (
        "YOUR OVERRIDE — against the market regime (HOSTILE): the desk's held-out longs here averaged −1.61% per "
        f"trade ≈ a loss of {x} on this position")
    y = NSE.money(0.0161 * 40 * 100)
    assert _verdict.regime_override_note(v, NSE, notional=4_000.0).endswith(f"≈ a loss of {y} on this position")


def test_h3_fix1_us_hostile_has_no_measured_figure_anywhere():
    """The −1.61% is NSE's held-out result: a US HOSTILE check and override never quote it."""
    us = _h3_verdict("HOSTILE", profile=US)
    reg = next(c for c in us.checks if c.name == "regime")
    assert reg.status is Status.BLOCK
    assert reg.reason == "Market regime HOSTILE: swing longs historically lose here"
    assert _verdict.regime_override_note(us, US) == "YOUR OVERRIDE — against the market regime (HOSTILE)"


def test_h3_override_note_without_a_held_out_figure_or_a_regime_block():
    unknown = _h3_verdict(None)  # regime unknown is treated as HOSTILE and blocks swing longs
    assert _verdict.regime_override_note(unknown, NSE) == ("YOUR OVERRIDE — against an unknown regime "
                                                           "(treated as HOSTILE)")
    assert _verdict.regime_override_note(_h3_verdict("TRENDING"), NSE) == ""
    assert _verdict.regime_override_note(_h3_verdict("MIXED"), NSE) == ""  # a WARN is advice, not a block


def test_h3_fix3_regime_name_comes_from_the_value_not_the_check_text():
    v = _h3_verdict("HOSTILE")
    odd = replace(v, checks=tuple(replace(c, reason="sit out") if c.name == "regime" else c for c in v.checks))
    assert _verdict.regime_override_note(odd, NSE).startswith(
        "YOUR OVERRIDE — against the market regime (HOSTILE): the desk's held-out longs")


def test_h3_fix3_check_trade_carries_the_regime_value(tmp_path):
    v = check_trade(rising(), "ABC", NSE, Mode.SWING, Limits(capital=1_000_000),
                    CheckContext(NSE, Limits(capital=1_000_000), NOW, regime=" hostile "),
                    audit_path=tmp_path / "a.jsonl")
    assert v.regime == "HOSTILE"
    assert check_trade(rising(), "ABC", NSE, Mode.SWING, Limits(capital=1_000_000),
                       CheckContext(NSE, Limits(capital=1_000_000), NOW), audit_path=tmp_path / "a.jsonl").regime is None


# ---- H5a: long verdicts and their input hashes are pinned before shorts exist ----
def _pin_ctx(**kw):
    return replace(_ctx_ok(), **kw)


def test_h5a_long_input_hashes_are_pinned():
    from tests.desk.test_plan import sessions15
    pi = replace(P, mode=Mode.INTRADAY, entry_limit=100.5)
    assert decide(pi, S, None, OK, NSE, 100.0, NOW).input_hash == \
        "e3b1691a5a26d4940a15cd72baa60f9828d6de3210becacfe8a12415ce4158ce"
    assert decide(P, S, None, OK, NSE, 100.0, NOW).input_hash == \
        "b3cfff9c411d5bc07b69bb3dc0dfbc5deb995a4ae910f09c22f927f06565d421"
    v = check_trade(rising(), "ABC", NSE, Mode.SWING, Limits(capital=100_000), _pin_ctx(), replays=1000, audit=False)
    assert v.input_hash == "fcc738e95f339dfbd50c83e57018956c358a40042ce636774fe8e2eb697e8ccc"
    now = datetime(2026, 10, 29, 10, 20, tzinfo=ZoneInfo("Asia/Kolkata"))
    v = check_trade(sessions15(), "ABC", NSE, Mode.INTRADAY, Limits(capital=100_000),
                    _pin_ctx(now=now, regime="HOSTILE"), replays=1000, audit=False, robustness=False)
    assert v.input_hash == "b26390f38c88148c1a180095b75e32a18895d5b6c49bfbf85eff6ed2c856e399"


def test_h5a_hash_includes_side_only_for_shorts():
    from nse_backtest.desk.models import Side
    from nse_backtest.desk.verdict import input_hash
    import dataclasses
    assert "side" in {f.name for f in dataclasses.fields(TradePlan)}
    assert [f.name for f in dataclasses.fields(TradePlan)][-1] == "side"
    short = TradePlan("ABC", "NSE", Mode.INTRADAY, 100, 102, 97, 94, 2, 15, 100, "2026-10-28T00:00:00", "user",
                      99.5, Side.SHORT)
    long = replace(short, side=Side.LONG)
    assert input_hash(short, S, None, OK, 100.0) != input_hash(long, S, None, OK, 100.0)


# ---- H5a: short tickets ----
def _short_plan(**kw):
    from nse_backtest.desk.models import Side
    base = TradePlan("ABC", "NSE", Mode.INTRADAY, 100.0, 102.0, 97.0, 94.0, 1.0, 18, 100.0,
                     "2026-10-29T09:30:00", "opening-range mid", 99.5, Side.SHORT)
    return replace(base, **kw)


@pytest.mark.parametrize("cp, first", [
    (100.3, "SELL 250 ABC MIS SL trigger 100.00 limit 99.50"),   # above the trigger: the sell stop-limit
    (None, "SELL 250 ABC MIS SL trigger 100.00 limit 99.50"),    # no price: the stop-limit
    (100.0, "SELL 250 ABC MIS LIMIT 99.50"),                     # at the trigger: a plain sell limit at the limit
    (99.7, "SELL 250 ABC MIS LIMIT 99.50"),                      # below it
])
def test_h5a_short_ticket_both_price_branches(cp, first):
    lines = ticket_lines(_short_plan(), S, NSE, current_price=cp, day=date(2026, 10, 29))
    assert lines == (first,
                     "Cancel the entry if not filled by 14:45 IST",
                     "SL-M BUY 250 ABC trigger 102.00",
                     "BUY 250 ABC LIMIT 97.00",
                     "Square off yourself by 15:10 IST (broker auto square-off charges ₹59+)")


def test_h5a_short_ticket_on_an_early_close_squares_off_earlier(monkeypatch):
    from datetime import time
    from nse_backtest.desk import calendars as cal
    d = date(2026, 10, 29)
    monkeypatch.setitem(cal.EARLY_CLOSES, "NSE", {d: time(14, 0)})  # fictional early close: flat-by 13:45
    lines = ticket_lines(_short_plan(), S, NSE, current_price=100.3, day=d)
    assert lines[1] == "Cancel the entry if not filled by 13:45 IST"
    assert lines[-1] == "Square off yourself by 13:45 IST (broker auto square-off charges ₹59+)"


def test_h5a_short_ticket_refuses_us_and_swing():
    with pytest.raises(ValueError, match="NSE intraday only"):
        ticket_lines(_short_plan(market="US"), S, US)
    with pytest.raises(ValueError, match="NSE intraday only"):
        ticket_lines(_short_plan(mode=Mode.SWING), S, NSE)


def test_h5a_long_tickets_unchanged():
    pi = replace(P, mode=Mode.INTRADAY, entry_limit=100.5)
    assert ticket_lines(pi, S, NSE, current_price=99.0, day=date(2026, 10, 29)) == (
        "BUY 250 ABC MIS SL trigger 100.00 limit 100.50", "Cancel the entry if not filled by 14:45 IST",
        "SL-M SELL 250 ABC trigger 98.00", "SELL 250 ABC LIMIT 103.00", "Square off by 15:15 IST")
