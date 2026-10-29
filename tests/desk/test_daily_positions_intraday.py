from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd, pytest
from nse_backtest.desk.positions import review_positions
from nse_backtest.desk.daily import daily_plan, flip_conditions, breakout_entry
from nse_backtest.desk.intraday import orb_scan
from nse_backtest.desk.checks import CheckContext
from nse_backtest.desk.models import Action, Limits
from nse_backtest.desk.markets import NSE
from nse_backtest.desk.nse_lists import BanList

@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))


NOW = datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def series(n=400, step=1.004, end="2026-10-28"):
    idx = pd.bdate_range(end=end, periods=n)
    close = 100 * step ** np.arange(1, n + 1); prev = close / step
    return pd.DataFrame({"Open": prev, "High": prev * 1.012, "Low": prev * 0.995, "Close": close, "Volume": 5e6}, index=idx)


def ctx():
    return CheckContext(profile=NSE, limits=Limits(capital=100_000), now=NOW, governor_can_trade=True,
                        ban=BanList(frozenset(), "ok", None, ""), earnings_checked=True)


def test_review_positions_orders_and_filters():
    pos = [{"symbol": "AAA", "buy_price": 100, "qty": 10, "stop_loss": 95, "target": 120, "entry_date": "2026-09-01"},
           {"symbol": "MISSING", "buy_price": 50, "qty": 1, "stop_loss": 45, "target": 60},
           {"symbol": "USX", "market": "US", "buy_price": 10, "qty": 1, "stop_loss": 9, "target": 12},
           {"symbol": "OLD", "closed": True, "buy_price": 1, "qty": 1, "stop_loss": 0.5, "target": 2}]
    out = review_positions(pos, {"AAA": series()}, NSE)
    assert [o.symbol for o in out] == ["MISSING", "AAA"]
    assert out[0].action == "DATA UNAVAILABLE" and out[1].r_so_far is not None


def test_flip_conditions():
    assert flip_conditions(series(step=1.004), NSE) == ()
    falling = flip_conditions(series(step=0.997), NSE)
    assert any("50-EMA" in s for s in falling) and any("60-day" in s for s in falling)
    assert flip_conditions(None, NSE) == ("Regime index data unavailable",)


def test_breakout_entry_modes():  # D1: (kind, price) instead of a NaN sentinel
    df = series()
    assert breakout_entry(df) == ("at", None)               # making new highs -> plan at close
    df2 = df.copy(); df2.iloc[-1, df2.columns.get_loc("Close")] = df2.High.tail(20).max() / 1.03
    kind, px = breakout_entry(df2)
    assert kind == "trigger" and px == pytest.approx(df2.High.tail(20).max() * 1.001)
    df3 = df.copy(); df3.iloc[-1, df3.columns.get_loc("Close")] = df3.High.tail(20).max() / 1.2
    assert breakout_entry(df3) == ("far", None)


def test_daily_plan_hostile_puts_ready_setups_on_watch():
    frames = {"AAA": series(), "BBB": series(), "SHORT": series(n=50)}
    dp = daily_plan(NSE, frames, series(step=0.997), Limits(capital=100_000), ctx(), replays=500)
    assert dp.regime == "HOSTILE" and dp.take == ()
    assert {v.plan.symbol for v in dp.watch} == {"AAA", "BBB"} and dp.unavailable == ("SHORT",)
    assert dp.flip_conditions


def test_orb_scan_returns_failures_not_silence():
    bad = pd.DataFrame({"Open": [], "High": [], "Low": [], "Close": []})
    verdicts, failures = orb_scan(NSE, {"EMPTY": bad}, Limits(capital=100_000), ctx(), replays=200)
    assert verdicts == [] and failures and failures[0][0] == "EMPTY"


def _trending_plan(**kw):
    frames = {"AAA": series(), "BBB": series(step=1.003), "CCC": series(step=1.0035), "DDD": series(step=1.0045)}
    return daily_plan(NSE, frames, series(step=1.004), Limits(capital=100_000), ctx(), replays=500, **kw)


def test_daily_plan_trending_orders_and_overflow(tmp_path):
    audit = tmp_path / "audit.jsonl"
    dp = _trending_plan(top_take=2, audit_path=audit)
    assert dp.regime == "TRENDING" and len(dp.take) == 2
    exps = [v.odds.mc_expectancy_r for v in dp.take]
    assert exps == sorted(exps, reverse=True)
    assert any("(beyond top 2" in why for _, why in dp.skipped)
    assert len(audit.read_text().strip().splitlines()) >= 2


def test_daily_plan_earnings_lookup_only_for_take_candidates():
    calls = []
    from datetime import date, timedelta
    def lookup(sym):
        calls.append(sym)
        return date(2026, 10, 29) + timedelta(days=2), True
    dp = _trending_plan(top_take=2, earnings_lookup=lookup)
    assert len(calls) == 2 and len(dp.take) >= 1  # 4 TAKE-able frames, only top 2 looked up
    for v in dp.take:
        assert any(c.name == "earnings" and c.status.value == "WARN" for c in v.checks)


def test_a2_unknown_regime_daily_plan_lands_on_watch():
    frames = {"AAA": series(), "BBB": series()}
    dp = daily_plan(NSE, frames, None, Limits(capital=100_000), ctx(), replays=500)
    assert dp.regime is None and dp.take == ()
    assert {v.plan.symbol for v in dp.watch} == {"AAA", "BBB"}
    for v in dp.watch:
        assert [c.name for c in v.blocks()] == ["regime"]


def test_a7_daily_plan_uses_given_regime_and_tape(monkeypatch):
    from nse_backtest.desk import daily
    def boom(df):
        raise AssertionError("assess_tape must not be called")
    monkeypatch.setattr(daily.tape_monitor, "assess_tape", boom)
    dp = daily_plan(NSE, {"AAA": series()}, series(step=0.997), Limits(capital=100_000), ctx(), replays=300,
                    regime="TRENDING", tape=None)
    assert dp.regime == "TRENDING"


def test_a7_daily_plan_assess_tape_exception_degrades_to_none(monkeypatch):
    from nse_backtest.desk import daily
    monkeypatch.setattr(daily.tape_monitor, "assess_tape", lambda df: (_ for _ in ()).throw(RuntimeError("x")))
    dp = daily_plan(NSE, {"AAA": series()}, series(), Limits(capital=100_000), ctx(), replays=300)
    assert dp.regime is None and dp.take == ()


def test_a5_daily_plan_quote_lookup_only_for_candidates():
    from datetime import timedelta
    from nse_backtest.desk.models import LiveQuote
    asked = []
    frames = {"AAA": series(), "BBB": series(step=1.003), "FAR": series()}
    far = frames["FAR"].copy(); far.iloc[-1, far.columns.get_loc("Close")] = far.High.tail(20).max() / 1.2
    frames["FAR"] = far
    def lookup(sym):
        asked.append(sym)
        px = float(frames[sym]["Close"].iloc[-1])
        return LiveQuote(price=px, day_open=px, as_of=NOW - timedelta(minutes=1))
    dp = daily_plan(NSE, frames, series(step=1.004), Limits(capital=100_000), ctx(), replays=300,
                    top_take=1, quote_lookup=lookup)
    assert "FAR" not in asked and 1 <= len(asked) <= 2
    for v in dp.take + dp.watch:
        if v.plan.symbol in asked:
            assert [c for c in v.checks if c.name == "live_price"][0].status.value == "PASS"



# ---- Final fix wave D3 ----
def _fake_check(action):
    from types import SimpleNamespace
    return lambda p, df: SimpleNamespace(action=action, current_price=90.0, suggested_sl=None, pnl_pct=-10.0,
                                         reason="stop hit")


def test_d3_market_filter_normalises_case_and_none(monkeypatch):
    from nse_backtest import position_monitor
    monkeypatch.setattr(position_monitor, "daily_check", _fake_check("HOLD"))
    pos = [{"symbol": "AAA", "market": "nse", "buy_price": 100, "stop_loss": 95},
           {"symbol": "BBB", "market": None, "buy_price": 100, "stop_loss": 95},
           {"symbol": "CCC", "market": "us", "buy_price": 100, "stop_loss": 95}]
    out = review_positions(pos, {"AAA": series(), "BBB": series(), "CCC": series()}, NSE)
    assert sorted(o.symbol for o in out) == ["AAA", "BBB"]


def test_d3_unreadable_entry_stop_are_none_not_zero():
    out = review_positions([{"symbol": "AAA", "buy_price": "abc", "stop_loss": 95}], {}, NSE)
    assert out[0].action == "DATA UNAVAILABLE" and out[0].entry is None and out[0].stop == 95.0
    out = review_positions([{"symbol": "AAA", "buy_price": 100}], {}, NSE)
    assert out[0].entry == 100.0 and out[0].stop is None


def test_d3_initial_stop_none_never_masks_exit(monkeypatch):
    from nse_backtest import position_monitor
    monkeypatch.setattr(position_monitor, "daily_check", _fake_check("EXIT"))
    out = review_positions([{"symbol": "AAA", "buy_price": 100, "stop_loss": 95, "initial_stop": None}],
                           {"AAA": series()}, NSE)
    assert out[0].action == "EXIT" and out[0].r_so_far == pytest.approx((90 - 100) / 5)


# ---- Last round: gap needs a quote from market-today ----
def test_lr_today_gap_pct_requires_quote_dated_market_today():
    import numpy as np, pandas as pd
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from nse_backtest.desk.daily import today_gap_pct
    from nse_backtest.desk.models import LiveQuote
    IST, ET = ZoneInfo("Asia/Kolkata"), ZoneInfo("America/New_York")
    df = pd.DataFrame({"Close": [100.0, 101.0]}, index=pd.to_datetime(["2026-10-27", "2026-10-28"]))
    old = LiveQuote(price=101.0, day_open=103.02, as_of=datetime(2026, 10, 28, 15, 29, tzinfo=IST))
    assert today_gap_pct(df, old, datetime(2026, 10, 29, 8, 45, tzinfo=IST)) is None
    assert today_gap_pct(df, old, datetime(2026, 10, 29, 10, 0, tzinfo=IST)) is None
    fresh = LiveQuote(price=102.0, day_open=102.02, as_of=datetime(2026, 10, 29, 9, 20, tzinfo=IST))
    g = today_gap_pct(df, fresh, datetime(2026, 10, 29, 10, 0, tzinfo=IST))
    assert g == pytest.approx(1.01, abs=0.001)
    # US: quote at 15:59 ET 10-28 is stale at 8:45 IST 10-29 evaluated in ET-aware now
    usq = LiveQuote(price=101.0, day_open=101.0, as_of=datetime(2026, 10, 28, 15, 59, tzinfo=ET))
    assert today_gap_pct(df, usq, datetime(2026, 10, 29, 8, 0, tzinfo=ET)) is None
