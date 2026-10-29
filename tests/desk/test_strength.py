"""H6: the "holding up" watchlist — relative strength vs the index among stocks above their own 50-EMA. No network."""
import math

import numpy as np
import pandas as pd
import pytest

from nse_backtest.desk import strength


def _frame(closes, end="2026-10-28"):
    idx = pd.bdate_range(end=end, periods=len(closes))
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1e6}, index=idx)


def _trend(step, n=120, start=100.0, end="2026-10-28"):
    return _frame(start * step ** np.arange(n), end=end)


def _ret60(df):
    c = df["Close"]
    return (float(c.iloc[-1]) / float(c.iloc[-61]) - 1) * 100


def test_ranks_by_60_day_return_minus_the_index_and_excludes_symbols_below_their_50_ema():
    index = _trend(1.001)
    fr = {"SLOW": _trend(1.002), "FAST": _trend(1.006), "MID": _trend(1.004),
          # rallied over 60 days but has just fallen below its own 50-EMA: excluded
          "BROKE": _frame(list(100 * 1.008 ** np.arange(115)) + [100 * 1.008 ** 114 * 0.75] * 5)}
    assert fr["BROKE"]["Close"].iloc[-1] < fr["BROKE"]["Close"].ewm(span=50, adjust=False).mean().iloc[-1]
    assert _ret60(fr["BROKE"]) > _ret60(fr["SLOW"])  # it would rank if it were not below its EMA
    out = strength.holding_up(fr, index)
    assert [s for s, _ in out] == ["FAST", "MID", "SLOW"]
    for s, rs in out:
        assert rs == pytest.approx(_ret60(fr[s]) - _ret60(index))


def test_a_stock_falling_less_than_the_index_still_needs_to_be_above_its_own_ema():
    index = _trend(0.995)
    fr = {"LESSBAD": _trend(0.998), "UP": _trend(1.001)}
    out = dict(strength.holding_up(fr, index))
    assert "LESSBAD" not in out  # beat the index but below its own 50-EMA: not holding up
    assert out["UP"] > 0


def test_takes_at_most_n_and_defaults_to_ten():
    index = _trend(1.0)
    fr = {f"S{i:02d}": _trend(1.001 + i / 10_000) for i in range(15)}
    out = strength.holding_up(fr, index)
    assert len(out) == 10 and out[0][0] == "S14"
    assert [s for s, _ in strength.holding_up(fr, index, n=3)] == ["S14", "S13", "S12"]


def test_ties_break_on_symbol_so_the_list_is_deterministic():
    index = _trend(1.0)
    fr = {"B": _trend(1.002), "A": _trend(1.002)}
    assert [s for s, _ in strength.holding_up(fr, index)] == ["A", "B"]


@pytest.mark.parametrize("index", [None, "short", "nan", "empty", "nocol"])
def test_fails_closed_when_the_index_is_missing_or_unusable(index):
    fr = {"UP": _trend(1.004)}
    df = {None: None, "short": _trend(1.001, n=40), "empty": _trend(1.0).iloc[:0],
          "nocol": _trend(1.0).drop(columns=["Close"])}.get(index)
    if index == "nan":
        df = _trend(1.001)
        df.iloc[-61, df.columns.get_loc("Close")] = math.nan
    assert strength.holding_up(fr, df) == []
    assert strength.index_problem(df)  # a reason the page shows


def test_index_problem_is_none_for_a_usable_index():
    assert strength.index_problem(_trend(1.001)) is None


def test_symbols_with_too_little_bad_or_stale_data_are_left_out_never_raise():
    index = _trend(1.001)
    bad = _trend(1.004)
    bad.iloc[-1, bad.columns.get_loc("Close")] = math.nan
    fr = {"OK": _trend(1.004), "SHORT": _trend(1.004, n=30), "NONE": None, "NAN": bad,
          "STALE": _trend(1.006, end="2026-10-20"),  # its last bar is not the index's: not comparable
          "NOCOL": _trend(1.004).drop(columns=["Close"])}
    assert [s for s, _ in strength.holding_up(fr, index)] == ["OK"]
