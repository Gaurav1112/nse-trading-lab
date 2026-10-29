"""C7 fix round: intraday band inputs of the trade card (pure helpers in components/desk_card.py)."""
import numpy as np
import pandas as pd

from components import desk_card
from nse_backtest.desk.markets import NSE
from nse_backtest.desk.models import Mode


def _bars(days=("2026-10-27", "2026-10-28"), today_bars=10, today="2026-10-29", gap=1.10):
    idx = []
    for d in days:
        idx += list(pd.date_range(f"{d} 09:15", periods=25, freq="15min"))
    idx += list(pd.date_range(f"{today} 09:15", periods=today_bars, freq="15min"))
    close = 100 * 1.001 ** np.arange(len(idx))
    close[25:] *= gap  # an overnight gap before the second session
    return pd.DataFrame({"Open": close, "High": close * 1.001, "Low": close * 0.999, "Close": close,
                         "Volume": 1e5}, index=pd.DatetimeIndex(idx))


def test_session_returns_drop_the_overnight_gap():
    b = _bars()
    r = desk_card.session_returns(b)
    assert len(r) == 24 + 24 + 9
    assert np.allclose(r, np.log(1.001))  # the 10% gap is not a return


def test_bars_left_until_flat_by():
    assert desk_card.bars_left(pd.Timestamp("2026-10-29 11:30"), NSE) == 14  # 11:45 .. 15:00 start before 15:15
    assert desk_card.bars_left(pd.Timestamp("2026-10-29 15:00"), NSE) == 0
    assert desk_card.bars_left(pd.Timestamp("2026-10-29 14:45"), NSE) == 1


def test_intraday_band_from_history_projects_to_flat_by():
    b = _bars()
    shown, band, why = desk_card.chart_inputs(b, Mode.INTRADAY, NSE, "2026-10-29")
    assert why is None and len(shown) == 10 and shown.index[0] == pd.Timestamp("2026-10-29 09:15")
    assert band.steps == desk_card.bars_left(b.index[-1], NSE) and band.start == b.index[-1]
    assert abs(band.p50[0] - b["Close"].iloc[-1]) / b["Close"].iloc[-1] < 0.01


def test_intraday_band_unavailable_cases():
    _, band, why = desk_card.chart_inputs(_bars(today_bars=25), Mode.INTRADAY, NSE, "2026-10-29")
    assert band is None and why == "session over"
    _, band, why = desk_card.chart_inputs(_bars(), Mode.INTRADAY, NSE, "2026-10-30")  # last bar not today
    assert band is None and why == "session over"
    _, band, why = desk_card.chart_inputs(_bars(days=(), today_bars=10), Mode.INTRADAY, NSE, "2026-10-29")
    assert band is None and why == "not enough intraday history yet"


def test_empty_frames_are_no_bars():
    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex([]))
    for mode in (Mode.INTRADAY, Mode.SWING):
        shown, band, why = desk_card.chart_inputs(empty, mode, NSE, "2026-10-29")
        assert shown is None and band is None and why == "no bars"


def test_swing_uses_last_120_daily_bars():
    idx = pd.bdate_range("2026-01-01", periods=300)
    b = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": np.linspace(100, 130, 300), "Volume": 1.0},
                     index=idx)
    shown, band, why = desk_card.chart_inputs(b, Mode.SWING, NSE, "2026-10-29", steps=15)
    assert len(shown) == 120 and band.steps == 15 and why is None


def test_markdown_escape_and_url_encoding():
    assert desk_card.md_escape("a*b_[x](y)") == r"a\*b\_\[x\]\(y\)"
    assert desk_card.safe_url("https://x.test/a(b)c") == "https://x.test/a%28b%29c"
    assert desk_card.safe_url("https://x.test/a(b)c d") is None  # F1 D2: a space (<= 0x20) is rejected
    assert desk_card.safe_url("javascript:alert(1)") is None


def test_f1_c2_bars_left_uses_the_early_close_flat_by():
    from nse_backtest.desk.markets import US
    assert desk_card.bars_left(pd.Timestamp("2026-11-27 12:00"), US) == 2  # flat by 12:45: 12:15, 12:30
    assert desk_card.bars_left(pd.Timestamp("2026-11-25 12:00"), US) == 15  # a normal day: flat by 15:55
