"""Plotly figure builders for the trade chooser. Pure: data in, ``go.Figure`` out; no Streamlit, network or I/O.

Every projection says "range of past behaviour, not a forecast". A missing input is an annotation on the figure,
never an empty-looking chart. Money axes take their prefix from ``profile.currency``.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from .markets import MarketProfile

NOT_A_FORECAST = "range of past behaviour, not a forecast"
_ONE_DAY = pd.Timedelta(days=1)


def _note(fig: go.Figure, text: str, *, y: float = 0.5) -> None:
    fig.add_annotation(text=text, xref="paper", yref="paper", x=0.5, y=y, showarrow=False)


def _col(bars: pd.DataFrame, name: str) -> pd.Series:
    return bars[name] if name in bars.columns else bars[name.lower()]


def _band_axis(bars: pd.DataFrame, band) -> pd.DatetimeIndex:
    """x values for the band: ``band.steps`` periods after ``band.start``. Swing bars (median spacing of a day or
    more) advance by business days (``bdate_range``); intraday bars advance by their own median spacing
    (15 minutes for the desk's 15-minute bars)."""
    start = pd.Timestamp(band.start)
    step = pd.Series(bars.index).diff().median() if len(bars) > 1 else _ONE_DAY
    if pd.isna(step) or step >= _ONE_DAY:
        return pd.bdate_range(start, periods=band.steps + 1)[1:]
    return pd.DatetimeIndex([start + step * (i + 1) for i in range(band.steps)])


def _hline(fig: go.Figure, y: float, label: str, color: str, dash: str = "solid") -> None:
    fig.add_shape(type="line", xref="paper", x0=0, x1=1, yref="y", y0=y, y1=y,
                  line={"color": color, "dash": dash, "width": 1.5})
    fig.add_annotation(text=f"{label} {y:,.2f}", xref="paper", x=1, yref="y", y=y, xanchor="left",
                       showarrow=False, font={"color": color, "size": 11})


def price_chart(bars: pd.DataFrame, card, band, profile: MarketProfile) -> go.Figure:
    """Candles with entry / stop / T1 / T2 (and the entry limit when set) as horizontal lines, plus the p5-p95 and
    p25-p75 range band when ``band`` is given; otherwise an annotation says the band is unavailable."""
    fig = go.Figure(go.Candlestick(x=list(bars.index), open=_col(bars, "Open"), high=_col(bars, "High"),
                                   low=_col(bars, "Low"), close=_col(bars, "Close"), name="Price"))
    _hline(fig, card.entry, "Entry", "#1f77b4")
    _hline(fig, card.stop, "Stop", "#d62728")
    _hline(fig, card.target_1, "Target 1", "#2ca02c")
    _hline(fig, card.target_2, "Target 2", "#2ca02c", "dash")
    if card.entry_limit is not None:
        _hline(fig, card.entry_limit, "Entry limit", "#1f77b4", "dot")
    if band is None:
        _note(fig, "range band unavailable")
    else:
        x = _band_axis(bars, band)
        for lo, hi, name, fill in ((band.p5, band.p95, "p5-p95", "rgba(100,100,100,0.15)"),
                                   (band.p25, band.p75, "p25-p75", "rgba(100,100,100,0.30)")):
            fig.add_trace(go.Scatter(x=x, y=list(hi), mode="lines", line={"width": 0}, showlegend=False,
                                     hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=x, y=list(lo), mode="lines", line={"width": 0}, fill="tonexty",
                                     fillcolor=fill, name=name))
    fig.update_layout(title=f"Price and levels ({NOT_A_FORECAST})", xaxis_rangeslider_visible=False,
                      yaxis={"tickprefix": profile.currency}, margin={"r": 110})
    return fig


def outcome_chart(om, card, profile: MarketProfile) -> go.Figure:
    """Histogram of money outcomes per replay with vertical lines at p5, median and p95."""
    if card.qty == 0:
        raise ValueError("no outcome chart for a zero-quantity card")
    fig = go.Figure(go.Histogram(x=list(om.pnl), name="Outcomes"))
    for x, label in ((om.p5, "p5"), (om.median, "Median"), (om.p95, "p95")):
        fig.add_shape(type="line", xref="x", x0=x, x1=x, yref="paper", y0=0, y1=1, line={"dash": "dash", "width": 1.5})
        fig.add_annotation(text=f"{label} {profile.money(x)}", xref="x", x=x, yref="paper", y=1, showarrow=False,
                           yanchor="bottom")
    fig.update_layout(title=f"Money outcome per trade ({NOT_A_FORECAST})", xaxis={"tickprefix": profile.currency},
                      margin={"t": 90})
    return fig


def account_chart(ap, profile: MarketProfile) -> go.Figure:
    """p5 / p50 / p95 account value after each of ``ap.n_trades`` trades, with the drawdown and finish-down odds."""
    x = list(range(ap.n_trades + 1))
    fig = go.Figure()
    for series, name in ((ap.p95, "p95"), (ap.p50, "p50"), (ap.p5, "p5")):
        fig.add_trace(go.Scatter(x=x, y=list(series), mode="lines", name=name))
    _note(fig, f"{ap.p_dd_10:.0%} of paths fall 10% or more from the start; "
               f"{ap.p_finish_down:.0%} finish below it ({ap.source})", y=1.0)
    fig.update_layout(title=f"Account after {ap.n_trades} trades ({NOT_A_FORECAST})",
                      xaxis={"title": "Trades"}, yaxis={"tickprefix": profile.currency}, margin={"t": 110})
    return fig


def projected_vs_real_chart(cum_proj, cum_real, pairs) -> go.Figure:
    """Cumulative projected R against cumulative realised R, in trade order."""
    fig = go.Figure()
    if len(cum_real) == 0:
        _note(fig, "no resolved trades yet")
    else:
        fig.add_trace(go.Scatter(x=list(range(1, len(cum_real) + 1)), y=list(cum_real), mode="lines+markers",
                                 name="Realised R"))
        if len(cum_proj) == 0:
            _note(fig, "no projections were recorded for these trades")
        else:
            fig.add_trace(go.Scatter(x=list(range(1, len(cum_proj) + 1)), y=list(cum_proj), mode="lines+markers",
                                     name="Projected R"))
    fig.update_layout(title=f"Cumulative R: projected vs what happened ({len(pairs)} predicted trades)",
                      xaxis={"title": "Resolved trades"}, yaxis={"title": "Cumulative R"})
    return fig
