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
