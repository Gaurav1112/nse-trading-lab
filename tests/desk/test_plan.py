import numpy as np, pandas as pd, pytest
from nse_backtest.desk.plan import atr, atr_series, build_plan, swing_stop
from nse_backtest.desk.models import Mode
from nse_backtest.desk.markets import NSE


def flat(n=30):
    idx = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1e6}, index=idx)


def test_atr_constant_range():
    assert atr(flat()) == pytest.approx(2.0)


def test_swing_stop_clamped_to_one_atr_floor():
    p = build_plan(flat(), "X", Mode.SWING, NSE)
    assert p.entry == 100 and p.stop == pytest.approx(98.0) and p.stop_basis == "1×ATR floor"
    assert p.target_1 == pytest.approx(103.0) and p.target_2 == pytest.approx(106.0)
    assert p.market == "NSE" and p.max_hold_bars == 15


def test_swing_stop_uses_2atr_when_swing_low_far():
    df = flat()
    df.iloc[-10, df.columns.get_loc("Low")] = 80.0
    a = (13 * 2 + 21) / 14
    p = build_plan(df, "X", Mode.SWING, NSE)
    assert p.atr == pytest.approx(a)
    assert p.stop == pytest.approx(100 - 2 * a) and p.stop_basis == "2×ATR"


def test_swing_stop_helper_prefers_higher_stop():
    assert swing_stop(100, 5, 95.0) == (pytest.approx(94.905), "10-bar swing low")


def test_user_overrides():
    p = build_plan(flat(), "X", Mode.SWING, NSE, entry=100, stop=95, target=110)
    assert (p.stop, p.stop_basis, p.target_1, p.target_2) == (95, "user", 110, 115)


@pytest.mark.parametrize("kw", [dict(stop=100), dict(stop=101), dict(target=99)])
def test_rejects_bad_geometry(kw):
    with pytest.raises(ValueError):
        build_plan(flat(), "X", Mode.SWING, NSE, entry=100, **kw)


def test_rejects_short_history():
    with pytest.raises(ValueError):
        build_plan(flat(10), "X", Mode.SWING, NSE)


def test_intraday_opening_range_plan():
    idx = pd.date_range("2026-10-28 09:15", periods=6, freq="15min")
    df = pd.DataFrame({"Open": [100, 101, 102, 102, 103, 103], "High": [102, 103, 103, 104, 104, 104],
                       "Low": [99, 100, 101, 101, 102, 102], "Close": [101, 102, 102, 103, 103, 104],
                       "Volume": 1e5}, index=idx)
    p = build_plan(df, "X", Mode.INTRADAY, NSE)
    assert p.entry == 102 and p.stop == pytest.approx(100.5) and p.stop_basis == "opening-range mid"
    # A8: T1 = entry + 1.5R (meets the default min_rr), T2 = entry + 3R
    assert p.target_1 == pytest.approx(104.25) and p.target_2 == pytest.approx(106.5)
    assert p.rr_t1 == pytest.approx(1.5)
    # last bar 10:30 -> flat by 15:15 = 19 bars of 15 min
    assert p.max_hold_bars == 19


def test_atr_series_matches_atr_and_has_early_values():
    df = flat()
    s = atr_series(df)
    assert s.iloc[-1] == pytest.approx(atr(df)) and not s.isna().any()


def test_atr_series_early_values_use_available_bars():
    df = pd.DataFrame({"Open": [9.0, 11.0, 12.0], "High": [10.0, 12.0, 13.0],
                       "Low": [8.0, 9.0, 11.0], "Close": [9.0, 11.0, 12.0], "Volume": 1e6},
                      index=pd.bdate_range("2026-01-01", periods=3))
    s = atr_series(df)
    # TR0 = H-L = 2 (no prev close); TR1 = max(3, |12-9|, |9-9|) = 3
    assert s.iloc[0] == pytest.approx(df.High.iloc[0] - df.Low.iloc[0]) == pytest.approx(2.0)
    assert s.iloc[1] == pytest.approx((2.0 + 3.0) / 2)


def test_swing_nan_close_raises():
    df = flat()
    df.iloc[-1, df.columns.get_loc("Close")] = np.nan
    with pytest.raises(ValueError, match="non-finite price data: entry"):
        build_plan(df, "X", Mode.SWING, NSE)


def test_swing_nan_lows_make_stop_nan_raises():
    df = flat()
    df.iloc[-10:, df.columns.get_loc("Low")] = np.nan  # min() of the last 10 lows is NaN
    with pytest.raises(ValueError, match="non-finite price data: stop"):
        build_plan(df, "X", Mode.SWING, NSE, entry=100)


def test_intraday_nan_opening_range_high_raises():
    idx = pd.date_range("2026-10-28 09:15", periods=6, freq="15min")
    df = pd.DataFrame({"Open": 100.0, "High": [102, 103, 103, 104, 104, 104], "Low": 99.0,
                       "Close": 101.0, "Volume": 1e5}, index=idx)
    df.iloc[0, df.columns.get_loc("High")] = np.nan
    with pytest.raises(ValueError, match="non-finite price data"):
        build_plan(df, "X", Mode.INTRADAY, NSE)


def test_intraday_ignores_previous_session_bars():
    prev = pd.date_range("2026-10-27 09:15", periods=4, freq="15min")
    today = pd.date_range("2026-10-28 09:15", periods=4, freq="15min")
    idx = prev.append(today)
    high = [500.0] * 4 + [102.0, 103.0, 103.0, 104.0]
    low = [1.0] * 4 + [99.0, 100.0, 101.0, 101.0]
    df = pd.DataFrame({"Open": 100.0, "High": high, "Low": low, "Close": 101.0, "Volume": 1e5}, index=idx)
    p = build_plan(df, "X", Mode.INTRADAY, NSE)
    # opening range comes from 2026-10-28 09:15 only, not the 500/1 previous-day bars
    assert p.entry == 102 and p.stop == pytest.approx((102 + 99) / 2)


@pytest.mark.parametrize("last", ["2026-10-28 15:15", "2026-10-28 15:30"])
def test_intraday_hold_is_at_least_one_bar_at_or_after_flat_by(last):
    idx = pd.date_range(end=last, periods=3, freq="15min")
    df = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1e5}, index=idx)
    p = build_plan(df, "X", Mode.INTRADAY, NSE)
    assert p.max_hold_bars == 1


def test_validation_errors():
    with pytest.raises(ValueError, match="Low"):
        atr(flat().drop(columns=["Low"]))
    with pytest.raises(ValueError):
        atr(flat(0))
    with pytest.raises(ValueError, match="need at least 20 bars"):
        build_plan(flat(10), "X", Mode.SWING, NSE)


# ---- Final fix wave A4 ----
def test_a4_nonfinite_last_close_raises_even_with_user_geometry():
    df = flat()
    df.iloc[-1, df.columns.get_loc("Close")] = np.nan
    with pytest.raises(ValueError, match="non-finite price data: last_close"):
        build_plan(df, "X", Mode.SWING, NSE, entry=100, stop=98, target=103)


@pytest.mark.parametrize("bad", ["dup", "unsorted"])
def test_a4_bars_out_of_time_order_raise(bad):
    df = flat()
    df = pd.concat([df, df.iloc[[-1]]]) if bad == "dup" else df.iloc[::-1]
    with pytest.raises(ValueError, match="time order"):
        build_plan(df, "X", Mode.SWING, NSE)
