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


# ---------------------------------------------------------------- fix round 1
from nse_backtest.desk.markets import get_profile
from nse_backtest.desk.plan import atr_series, swing_stop


def intraday_frame(*sessions):
    """sessions: (date, [(HH:MM, o, h, l, c), ...]); a trailing flat session is appended (latest is excluded)."""
    rows, idx = [], []
    for day, bars in sessions:
        for hhmm, o, h, l, c in bars:
            idx.append(pd.Timestamp(f"{day} {hhmm}")); rows.append((o, h, l, c))
    idx.append(pd.Timestamp("2026-11-30 09:15")); rows.append((100, 101, 99, 100))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=pd.DatetimeIndex(idx))


def iplan():
    return TradePlan("X", "NSE", Mode.INTRADAY, 102.0, 100.0, 105.0, 108.0, 1.0, 20, 102.0, "x", "opening-range mid")


FLAT = (100, 101, 99, 100)


def test_intraday_breakout_after_no_entry_window_is_no_trade():
    bars = [("09:15", 100, 101, 99, 100)]
    t = pd.Timestamp("2026-10-27 09:30")
    while t.time() <= pd.Timestamp("2026-10-27 14:30").time():
        bars.append((t.strftime("%H:%M"), *FLAT)); t += pd.Timedelta(minutes=15)
    bars.append(("14:45", 101, 104, 101, 103))  # first close above OR high is outside the window
    bars.append(("15:00", 103, 109, 102.5, 108))  # a later bar that would hit target if the trade were taken
    assert setup_history(intraday_frame(("2026-10-27", bars)), iplan()) == []


def test_intraday_breakout_at_0945_with_later_bars_is_recorded():
    bars = [("09:15", 100, 101, 99, 100), ("09:30", *FLAT), ("09:45", 101, 102.5, 100.5, 102),
            ("10:00", 102, 106, 101.5, 105.5)]
    assert setup_history(intraday_frame(("2026-10-27", bars)), iplan()) == [pytest.approx(1.5)]


def test_intraday_breakout_with_no_later_bars_is_time_exit_at_close():
    bars = [("09:15", 100, 101, 99, 100), ("09:30", 101, 102.5, 100.5, 102)]
    rs = setup_history(intraday_frame(("2026-10-27", bars)), iplan(), cost_per_share=0.5)
    assert rs == [pytest.approx(-0.5 / 2.0)]  # 0R minus cost, risk = 102 - 100


def test_intraday_session_without_open_bar_is_skipped():
    bars = [("09:30", 100, 101, 99, 100), ("09:45", 101, 102.5, 100.5, 102), ("10:00", 102, 106, 101.5, 105.5)]
    assert setup_history(intraday_frame(("2026-10-27", bars)), iplan()) == []


def test_intraday_degenerate_zero_range_open_is_skipped():
    wide = [(f"{h:02d}:{m:02d}", *(100, 101, 99, 100)) for h, m in
            [(9, 15), (9, 30), (9, 45), (10, 0), (10, 15), (10, 30), (10, 45), (11, 0), (11, 15), (11, 30)]]
    bad = [("09:15", 100, 100, 100, 100), ("09:30", 100, 100.1, 100, 100.05), ("09:45", 100.05, 100.2, 100.0, 100.1)]
    df = intraday_frame(("2026-10-26", wide), ("2026-10-27", bad))
    assert setup_history(df, iplan()) == []


def test_swing_risk_guard_keeps_normal_uptrend_trades():
    # swing_stop floors risk at 1 ATR so the 0.1*ATR guard never removes a swing trade here
    assert len(setup_history(geometric(), plan(t1=104.2, stop=97.2))) >= 20


def test_monte_carlo_input_guards():
    r = const_ratios(1.0, 1.012, 0.998, 1.01)
    for args in [(100, 100, 103, 15), (100, 101, 103, 15), (100, 97, 100, 15), (100, 97, 99, 15),
                 (100, 97, 103, 0), (float("nan"), 97, 103, 15), (100, float("nan"), 103, 15),
                 (100, 97, float("inf"), 15)]:
        with pytest.raises(ValueError):
            monte_carlo(r, *args)
    with pytest.raises(ValueError):
        monte_carlo(r, 100, 97, 103, 15, cost_per_share=float("nan"))


def test_monte_carlo_gap_through_target_fills_at_open():
    pt, _, _, exp = monte_carlo(const_ratios(1.1, 1.12, 1.09, 1.1), 100, 97, 103, 15)
    assert pt == 1.0 and exp == pytest.approx(10 / 3)


def test_intraday_ratios_drop_bars_at_or_after_flat_by():
    def session(day):
        return pd.date_range(f"{day} 09:15", f"{day} 15:15", freq="15min")
    idx = session("2026-10-27").append(session("2026-10-28"))
    df = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0}, index=idx)
    assert bar_ratios(df, Mode.INTRADAY).shape == (48, 4)
    assert bar_ratios(df, Mode.INTRADAY, profile=get_profile("NSE")).shape == (46, 4)


# swing regression: trade fires at bar 200 of an uptrend; bars after it are rewritten by hand
def swing_case(n, hold):
    df = geometric(n=n)
    entry = float(df["Close"].iloc[200])
    stop, _ = swing_stop(entry, float(atr_series(df).iloc[200]), float(df["Low"].rolling(10).min().iloc[200]))
    risk = entry - stop
    p = plan(entry=100.0, stop=97.0, t1=104.5, hold=hold)  # rr_t1 = 1.5
    return df, entry, stop, risk, entry + 1.5 * risk, p


def set_bar(df, i, o, h, l, c):
    df.iloc[i, [df.columns.get_loc(k) for k in ("Open", "High", "Low", "Close")]] = [o, h, l, c]


def test_swing_stop_wins_when_bar_touches_both():
    df, entry, stop, risk, target, p = swing_case(202, 5)
    set_bar(df, 201, entry, target + 1, stop - 1, entry)
    assert setup_history(df, p) == [pytest.approx(-1.0)]


def test_swing_gap_through_stop_fills_at_open():
    df, entry, stop, risk, target, p = swing_case(202, 5)
    set_bar(df, 201, stop - risk, stop - risk, stop - 2 * risk, stop - risk)
    assert setup_history(df, p) == [pytest.approx(-2.0)]


def test_swing_time_exit_at_close_of_last_hold_bar():
    df, entry, stop, risk, target, p = swing_case(205, 3)
    for i in (201, 202):
        set_bar(df, i, entry, entry, entry, entry)
    set_bar(df, 203, entry + 0.3 * risk, entry + 0.3 * risk, entry + 0.3 * risk, entry + 0.3 * risk)
    assert setup_history(df, p) == [pytest.approx(0.3)]


def test_swing_unresolved_final_trade_is_discarded():
    df, entry, stop, risk, target, p = swing_case(204, 5)
    for i in (201, 202, 203):
        set_bar(df, i, entry, entry, entry, entry)
    assert setup_history(df, p) == []


def test_noise_expectancies_deterministic_under_seed():
    from nse_backtest.desk.timemachine import noise_expectancies
    a = noise_expectancies(geometric(), plan(t1=104.2, stop=97.2), runs=5, seed=3)
    assert a and a == noise_expectancies(geometric(), plan(t1=104.2, stop=97.2), runs=5, seed=3)
