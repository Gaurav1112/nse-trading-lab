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


def test_breakout_entry_modes():
    df = series()
    assert breakout_entry(df) is None                       # making new highs -> plan at close
    df2 = df.copy(); df2.iloc[-1, df2.columns.get_loc("Close")] = df2.High.tail(20).max() / 1.03
    assert breakout_entry(df2) == pytest.approx(df2.High.tail(20).max() * 1.001)
    df3 = df.copy(); df3.iloc[-1, df3.columns.get_loc("Close")] = df3.High.tail(20).max() / 1.2
    assert np.isnan(breakout_entry(df3))


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
