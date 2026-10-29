from types import SimpleNamespace

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from nse_backtest.desk.charts import account_chart, outcome_chart, price_chart, projected_vs_real_chart
from nse_backtest.desk.markets import NSE, US
from nse_backtest.desk.projections import AccountProjection, OutcomeMoney, PriceBand

DISCLAIMER = "range of past behaviour, not a forecast"


def _card(entry_limit=None, qty=10):
    return SimpleNamespace(entry=100.0, stop=95.0, target_1=108.0, target_2=115.0, entry_limit=entry_limit, qty=qty)


def _bars(n=40, freq="B"):
    idx = pd.date_range("2026-01-01", periods=n, freq=freq)
    c = np.linspace(90, 110, n)
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c}, index=idx)


def _band(bars, steps=5):
    up = lambda k: tuple(float(110 + k + i) for i in range(steps))
    return PriceBand(bars.index[-1], steps, up(-5), up(-2), up(0), up(2), up(5))


def _hlines(fig):
    return {round(s.y0, 6): s for s in fig.layout.shapes if s.y0 == s.y1}


def _texts(fig):
    return " | ".join(a.text for a in fig.layout.annotations)


def test_price_chart_candles_levels_and_band():
    bars = _bars()
    band = _band(bars)
    fig = price_chart(bars, _card(), band, NSE)
    candles = [t for t in fig.data if isinstance(t, go.Candlestick)]
    assert len(candles) == 1 and len(candles[0].x) == len(bars)
    assert set(_hlines(fig)) == {100.0, 95.0, 108.0, 115.0}
    text = _texts(fig)
    for label in ("Entry", "Stop", "Target 1", "Target 2"):
        assert label in text
    ys = [tuple(t.y) for t in fig.data if isinstance(t, go.Scatter)]
    for k in (band.p5, band.p95, band.p25, band.p75):
        assert k in ys
    band_x = [t for t in fig.data if isinstance(t, go.Scatter) and tuple(t.y) == band.p95][0].x
    assert len(band_x) == band.steps and pd.Timestamp(band_x[0]) > bars.index[-1]
    assert pd.Timestamp(band_x[0]).dayofweek < 5
    assert DISCLAIMER in fig.layout.title.text
    assert fig.layout.yaxis.tickprefix == "₹"
    assert "range band unavailable" not in text


def test_price_chart_no_band_and_intraday_limit():
    bars = _bars(30, "15min")
    fig = price_chart(bars, _card(entry_limit=99.5), None, US)
    assert "range band unavailable" in _texts(fig)
    assert not [t for t in fig.data if isinstance(t, go.Scatter)]
    assert set(_hlines(fig)) == {100.0, 95.0, 108.0, 115.0, 99.5}
    assert "Entry limit" in _texts(fig)
    assert fig.layout.yaxis.tickprefix == "$"
    fig2 = price_chart(bars, _card(), _band(bars, 4), US)
    xs = [t for t in fig2.data if isinstance(t, go.Scatter)][0].x
    assert pd.Timestamp(xs[1]) - pd.Timestamp(xs[0]) == pd.Timedelta("15min")


def _om():
    pnl = tuple(float(x) for x in np.linspace(-500, 900, 101))
    return OutcomeMoney(pnl, -430.0, -200.0, 200.0, 600.0, 830.0, 0.4, 0.4, 0.2)


def test_outcome_chart():
    om = _om()
    fig = outcome_chart(om, _card(), NSE)
    hist = [t for t in fig.data if isinstance(t, go.Histogram)]
    assert len(hist) == 1 and tuple(hist[0].x) == om.pnl
    assert {round(s.x0, 6) for s in fig.layout.shapes if s.x0 == s.x1} >= {-430.0, 200.0, 830.0}
    assert DISCLAIMER in fig.layout.title.text
    assert fig.layout.xaxis.tickprefix == "₹"


def test_outcome_chart_zero_qty_raises():
    with pytest.raises(ValueError):
        outcome_chart(_om(), _card(qty=0), NSE)


def test_account_chart():
    n = 6
    seq = lambda a: tuple(1000.0 + a * i for i in range(n + 1))
    ap = AccountProjection(n, 1000.0, seq(-5), seq(2), seq(9), 0.123, 0.456, "Monte Carlo replays")
    fig = account_chart(ap, US)
    lines = [t for t in fig.data if isinstance(t, go.Scatter)]
    ys = {tuple(t.y) for t in lines}
    assert {ap.p5, ap.p50, ap.p95} <= ys
    assert all(len(t.y) == n + 1 for t in lines)
    text = _texts(fig)
    assert "12%" in text and "46%" in text
    assert DISCLAIMER in fig.layout.title.text
    assert fig.layout.yaxis.tickprefix == "$"


def test_projected_vs_real_chart():
    fig = projected_vs_real_chart([0.5, 1.0, 1.2], [0.8, -0.2, 0.4, 1.0], [(0.5, 0.8), (0.5, -1.0), (0.2, 0.2)])
    ys = [tuple(t.y) for t in fig.data]
    assert (0.5, 1.0, 1.2) in ys and (0.8, -0.2, 0.4, 1.0) in ys
    assert "projected vs what happened" in fig.layout.title.text


def test_projected_vs_real_chart_missing_inputs_annotate():
    assert "no resolved trades" in _texts(projected_vs_real_chart([], [], []))
    assert "no projections" in _texts(projected_vs_real_chart([], [1.0], []))
