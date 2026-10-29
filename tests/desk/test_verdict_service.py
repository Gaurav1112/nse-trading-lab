import json
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd, pytest
from nse_backtest.desk.verdict import decide, ticket_lines
from nse_backtest.desk.service import check_trade
from nse_backtest.desk.checks import CheckContext
from nse_backtest.desk.models import Action, Check, Limits, Mode, Sizing, Status, TradePlan
from nse_backtest.desk.markets import NSE, US
from nse_backtest.desk.nse_lists import BanList

NOW = datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
P = TradePlan("ABC", "NSE", Mode.SWING, 100, 98, 103, 106, 2, 15, 100, "2026-10-28T00:00:00", "user")
S = Sizing(250, "position cap", 640.0, 25_000.0, 140.0, False, "250 shares")
OK = (Check("regime", Status.PASS, ""),)


def test_take_ticket_nse_swing():
    v = decide(P, S, None, OK, NSE, 100.0, NOW)
    assert v.action is Action.TAKE and v.ticket == ("BUY 250 ABC CNC LIMIT 100.00", "GTT OCO ABC: stop-loss 98.00 / target 103.00")
    assert v.headline == "TAKE ABC — BUY 250 @ ₹100.00, max loss ₹640.00" and len(v.input_hash) == 64


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
    mis = ticket_lines(replace(P, mode=Mode.INTRADAY), S, NSE)
    assert mis[0].startswith("BUY 250 ABC MIS LIMIT") and mis[-1] == "Square off by 15:15 IST"


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
