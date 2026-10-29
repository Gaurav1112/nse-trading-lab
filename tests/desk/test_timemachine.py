import numpy as np, pandas as pd, pytest
from nse_backtest.desk.timemachine import monte_carlo, setup_history, shuffle_drawdowns, odds, bar_ratios
from nse_backtest.desk.models import Mode, TradePlan


def geometric(n=400, step=1.01, h=1.012, l=0.998):
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = 100 * step ** np.arange(1, n + 1); prev = close / step
    return pd.DataFrame({"Open": prev, "High": prev * h, "Low": prev * l, "Close": close, "Volume": 1e6}, index=idx)


def plan(entry=100.0, stop=97.0, t1=103.0, hold=15):
    return TradePlan("X", "NSE", Mode.SWING, entry, stop, t1, t1 + 3, 1.4, hold, entry, "2026-10-28", "user")


def const_ratios(o, h, l, c, n=50):
    return np.tile([o, h, l, c], (n, 1)).astype(float)


def test_uptrend_hits_target_every_path():
    pt, ps, ptime, exp = monte_carlo(const_ratios(1.0, 1.012, 0.998, 1.01), 100, 97, 103, 15)
    assert (pt, ps, ptime) == (1.0, 0.0, 0.0) and exp == pytest.approx(1.0)


def test_time_exit_when_hold_too_short():
    pt, ps, ptime, exp = monte_carlo(const_ratios(1.0, 1.012, 0.998, 1.01), 100, 97, 103, 2)
    assert ptime == 1.0 and exp == pytest.approx((100 * 1.01 ** 2 - 100) / 3)


def test_stop_wins_when_bar_touches_both():
    pt, ps, _, exp = monte_carlo(const_ratios(1.0, 1.05, 0.95, 1.0), 100, 97, 103, 15)
    assert ps == 1.0 and exp == pytest.approx(-1.0)


def test_gap_through_stop_fills_at_open():
    _, ps, _, exp = monte_carlo(const_ratios(0.9, 0.91, 0.89, 0.9), 100, 97, 103, 15)
    assert ps == 1.0 and exp == pytest.approx(-10 / 3)


def test_costs_reduce_expectancy():
    _, _, _, exp = monte_carlo(const_ratios(1.0, 1.012, 0.998, 1.01), 100, 97, 103, 15, cost_per_share=0.3)
    assert exp == pytest.approx(0.9)


def test_deterministic_under_seed():
    rng = np.random.default_rng(0)
    r = np.column_stack([np.ones(300), 1 + rng.uniform(0, .03, 300), 1 - rng.uniform(0, .03, 300), 1 + rng.normal(0, .01, 300)])
    assert monte_carlo(r, 100, 97, 104, 15, seed=7) == monte_carlo(r, 100, 97, 104, 15, seed=7)
    a = monte_carlo(r, 100, 97, 104, 15)
    assert sum(a[:3]) == pytest.approx(1.0)


def test_rejects_short_history():
    with pytest.raises(ValueError):
        monte_carlo(const_ratios(1, 1, 1, 1, n=5), 100, 97, 103, 15)


def test_setup_history_uptrend_all_wins():
    rs = setup_history(geometric(), plan(t1=104.2, stop=97.2))   # rr_t1 = 1.5
    assert len(rs) >= 20 and all(r == pytest.approx(1.5) for r in rs)


def test_setup_history_downtrend_never_fires():
    assert setup_history(geometric(step=0.99, h=1.002, l=0.988), plan()) == []


def test_shuffle_all_losses():
    assert shuffle_drawdowns([-1.0] * 6) == (6.0, 6.0, 6.0, 1.0)


def test_shuffle_all_wins_and_too_few():
    assert shuffle_drawdowns([1.0] * 10) == (0.0, 0.0, 0.0, 0.0)
    assert shuffle_drawdowns([1.0] * 4) is None


def test_odds_bundle():
    o = odds(geometric(), plan(t1=104.2, stop=97.2), replays=2000)
    assert o.replays == 2000 and o.seed == 19 and o.history_n >= 20
    assert o.history_win_pct == 100.0 and o.noise_negative_frac == 0.0
    assert o.dd_p95_r == 0.0 and len(o.history_rs) == o.history_n


def test_odds_without_robustness():
    o = odds(geometric(), plan(t1=104.2, stop=97.2), replays=500, robustness=False)
    assert o.dd_p50_r is None and o.noise_negative_frac is None


def test_intraday_ratios_drop_session_first_bar():
    idx = pd.date_range("2026-10-27 09:15", periods=4, freq="15min").append(pd.date_range("2026-10-28 09:15", periods=4, freq="15min"))
    df = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0}, index=idx)
    assert bar_ratios(df, Mode.INTRADAY).shape == (6, 4)


def test_intraday_setup_history_breakout_hits_target():
    # session 1: OR 99-101, breakout close 102 (stop 100, R=2, T1 at 1.5R = 105), then bar reaching 106
    day1 = pd.date_range("2026-10-27 09:15", periods=4, freq="15min")
    day2 = pd.date_range("2026-10-28 09:15", periods=2, freq="15min")  # latest session, excluded
    o = [100, 101, 102, 103, 100, 100]; h = [101, 102.5, 103, 106, 101, 101]
    l = [99, 100.5, 101.5, 102.5, 99, 99]; c = [100, 102, 102.5, 105.5, 100, 100]
    df = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c}, index=day1.append(day2))
    p = TradePlan("X", "NSE", Mode.INTRADAY, 102.0, 100.0, 105.0, 108.0, 1.0, 20, 102.0, "x", "opening-range mid")
    assert setup_history(df, p) == [pytest.approx(1.5)]
