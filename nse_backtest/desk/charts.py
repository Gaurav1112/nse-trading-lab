"""Plotly figure builders for the trade chooser. Pure: data in, ``go.Figure`` out; no Streamlit, network or I/O.

Every projection says "range of past behaviour, not a forecast". A missing input is an annotation on the figure,
never an empty-looking chart. Money axes take their prefix from ``profile.currency``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

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


def _money_axis(values, profile: MarketProfile) -> dict:
    """Axis settings whose tick labels put the sign before the currency ("-$500", not "$-500")."""
    vals = [float(v) for v in values]
    ticks = [float(t) for t in np.linspace(min(vals), max(vals), 6)]
    return {"tickmode": "array", "tickvals": ticks, "ticktext": [profile.money(t) for t in ticks]}


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
    """Histogram of money outcomes per replay. Dashed lines mark p5, median and p95; solid lines mark the plan:
    the stop (``-card.max_loss``), T1 and, when different, T2.

    Raises ValueError for ``card.qty == 0``: there are no money figures then (``outcome_money`` returns None),
    so the caller is expected not to ask for this chart."""
    if card.qty == 0:
        raise ValueError("no outcome chart for a zero-quantity card")
    fig = go.Figure(go.Histogram(x=list(om.pnl), name="Outcomes"))
    marks = [(om.p5, "p5", "dash", "#7f7f7f"), (om.median, "Median", "dash", "#7f7f7f"),
             (om.p95, "p95", "dash", "#7f7f7f"), (-card.max_loss, "stop: max loss", "solid", "#d62728"),
             (card.profit_t1, "T1", "solid", "#2ca02c")]
    if card.profit_t2 is not None and card.profit_t2 != card.profit_t1:
        marks.append((card.profit_t2, "T2", "dot", "#2ca02c"))
    for n, (x, label, dash, color) in enumerate(marks):
        fig.add_shape(type="line", xref="x", x0=x, x1=x, yref="paper", y0=0, y1=1,
                      line={"dash": dash, "width": 1.5, "color": color})
        fig.add_annotation(text=f"{label} {profile.money(x)}", xref="x", x=x, yref="paper", y=1, showarrow=False,
                           yanchor="bottom", yshift=14 * (n % 2), font={"color": color, "size": 11})
    fig.update_layout(title=f"Money outcome per trade ({NOT_A_FORECAST})",
                      xaxis=_money_axis([*om.pnl, *(m[0] for m in marks)], profile), margin={"t": 100})
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
                      xaxis={"title": "Trades"}, yaxis=_money_axis([*ap.p5, *ap.p50, *ap.p95], profile), margin={"t": 110})
    return fig


def projected_vs_real_chart(cum_proj, cum_real, pairs, n_unpredicted) -> go.Figure:
    """Left: cumulative projected vs realised R over the SAME trades (those with a projection, in order).
    Right: projected R (x) against realised R (y) per trade, with a y = x reference. Arguments are the four values
    of ``projections.projected_vs_real``; trades without a projection are summarised in the title only."""
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Cumulative R", "Per trade"))
    if len(cum_real) == 0:
        _note(fig, "no resolved trades yet")
        fig.update_layout(title="Projected vs what happened")
        return fig
    if len(pairs) == 0:
        _note(fig, "no projections were recorded for these trades")
        fig.update_layout(title=f"Projected vs what happened ({n_unpredicted} trades without a projection)")
        return fig
    proj = [float(p) for p, _ in pairs]
    real = [float(r) for _, r in pairs]
    n = len(pairs)
    x = list(range(1, n + 1))
    fig.add_trace(go.Scatter(x=x, y=np.cumsum(proj).tolist(), mode="lines+markers", name="Projected R"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=np.cumsum(real).tolist(), mode="lines+markers", name="Realised R"), row=1, col=1)
    fig.add_trace(go.Scatter(x=proj, y=real, mode="markers", name="Trades"), row=1, col=2)
    lo, hi = min(proj + real), max(proj + real)
    fig.add_trace(go.Scatter(x=[lo, hi], y=[lo, hi], mode="lines", line={"dash": "dot", "color": "#7f7f7f"},
                             name="projected = realised"), row=1, col=2)
    fig.update_xaxes(title_text="Trade with a projection", row=1, col=1)
    fig.update_xaxes(title_text="Projected R", row=1, col=2)
    fig.update_yaxes(title_text="Realised R", row=1, col=2)
    other = float(cum_real[-1]) - sum(real)
    fig.update_layout(title=f"Projected vs what happened: {n} trades with a projection; {n_unpredicted} without one "
                            f"not shown (their realised total {other:+.2f}R)", margin={"t": 110})
    return fig
