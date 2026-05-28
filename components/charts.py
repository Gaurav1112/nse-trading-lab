import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from ta import momentum as ta_mom, volatility as ta_vol


def make_candlestick(df: pd.DataFrame, score=None, title: str = "", period: int = 250) -> go.Figure:
    d = df.iloc[-period:] if len(df) > period else df.copy()
    close = d["Close"]
    e20 = close.ewm(span=20, adjust=False).mean()
    e50 = close.ewm(span=50, adjust=False).mean()
    e200 = close.ewm(span=200, adjust=False).mean()

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                        row_heights=[0.6, 0.2, 0.2])

    fig.add_trace(go.Candlestick(x=d.index, open=d["Open"], high=d["High"], low=d["Low"],
                                  close=close, name="Price",
                                  increasing_line_color="#00FF87", decreasing_line_color="#FF3355",
                                  showlegend=False), row=1, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=e20, line=dict(color="#FFB800", width=1), name="EMA20"), row=1, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=e50, line=dict(color="#A78BFA", width=1), name="EMA50"), row=1, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=e200, line=dict(color="#00D4FF", width=1, dash="dot"), name="EMA200"), row=1, col=1)

    try:
        bb = ta_vol.BollingerBands(close, window=20, window_dev=2)
        fig.add_trace(go.Scatter(x=d.index, y=bb.bollinger_hband(),
                                  line=dict(color="#334155", width=1), showlegend=False), row=1, col=1)
        fig.add_trace(go.Scatter(x=d.index, y=bb.bollinger_lband(),
                                  line=dict(color="#334155", width=1),
                                  fill="tonexty", fillcolor="rgba(51,65,85,.08)",
                                  showlegend=False), row=1, col=1)
    except Exception:
        pass

    if score is not None:
        for price, label, color in [
            (score.stop_loss, f"SL ₹{score.stop_loss:.0f}", "#FF3355"),
            (score.target_1, f"T1 ₹{score.target_1:.0f}", "#00FF87"),
            (score.target_2, f"T2 ₹{score.target_2:.0f}", "#6ee7b7"),
        ]:
            fig.add_hline(y=price, line=dict(color=color, width=1, dash="dash"),
                          annotation_text=label, annotation_font_color=color, row=1, col=1)

    try:
        rsi = ta_mom.RSIIndicator(close, window=14).rsi()
        fig.add_trace(go.Scatter(x=d.index, y=rsi, line=dict(color="#FFB800", width=1),
                                  showlegend=False), row=2, col=1)
        fig.add_hline(y=70, line=dict(color="#FF3355", width=.5, dash="dot"), row=2, col=1)
        fig.add_hline(y=30, line=dict(color="#00FF87", width=.5, dash="dot"), row=2, col=1)
    except Exception:
        pass

    vol_colors = ["#00FF87" if c >= o else "#FF3355" for c, o in zip(d["Close"], d["Open"])]
    fig.add_trace(go.Bar(x=d.index, y=d["Volume"], marker_color=vol_colors, showlegend=False), row=3, col=1)

    fig.update_layout(template="plotly_dark", paper_bgcolor="#050A14", plot_bgcolor="#050A14",
                      title=dict(text=title, font=dict(color="#E8EDF5", size=14)),
                      margin=dict(l=10, r=10, t=40, b=10), height=720,
                      xaxis_rangeslider_visible=False, hovermode="x unified",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    fig.update_yaxes(gridcolor="#1A2540", zerolinecolor="#1E3A5F")
    fig.update_xaxes(gridcolor="#1A2540", rangebreaks=[dict(bounds=["sat","mon"])])
    return fig


def make_equity_curve(results_list: list[dict], labels: list[str] | None = None) -> go.Figure:
    colors = ["#4D9FFF","#00FF87","#FFB800","#A78BFA","#00D4FF","#FF3355",
              "#ec4899","#84cc16","#f97316","#a78bfa","#34d399"]
    fig = go.Figure()
    for i, result in enumerate(results_list):
        eq = result.get("equity_curve")
        if eq is None or (hasattr(eq, "empty") and eq.empty):
            continue
        label = (labels[i] if labels and i < len(labels)
                 else result.get("config", {}).get("strategy_name", f"Strategy {i+1}"))
        fig.add_trace(go.Scatter(x=eq.index, y=eq.values,
                                  line=dict(color=colors[i % len(colors)], width=1.5), name=label))
    if results_list:
        bh = results_list[0].get("buy_hold_curve")
        if bh is not None and not (hasattr(bh, "empty") and bh.empty):
            fig.add_trace(go.Scatter(x=bh.index, y=bh.values,
                                      line=dict(color="#64748b", width=1, dash="dot"), name="Buy & Hold"))
    fig.update_layout(template="plotly_dark", paper_bgcolor="#050A14", plot_bgcolor="#050A14",
                      title=dict(text="Strategy Equity Curves", font=dict(color="#E8EDF5")),
                      margin=dict(l=10,r=10,t=40,b=10), height=450, hovermode="x unified")
    fig.update_yaxes(gridcolor="#1A2540", tickprefix="₹")
    fig.update_xaxes(gridcolor="#1A2540")
    return fig


def make_sector_heat(sector_data: dict[str, float | None]) -> go.Figure:
    names = list(sector_data.keys())
    values = [v if v is not None else 0.0 for v in sector_data.values()]
    colors = ["#00FF87" if v >= 0 else "#FF3355" for v in values]
    text = [f"{sector_data[n]:+.2f}%" if sector_data[n] is not None else "N/A" for n in names]
    fig = go.Figure(go.Bar(x=names, y=values, marker_color=colors,
                            text=text, textposition="outside",
                            textfont=dict(color="#E8EDF5", size=11)))
    fig.update_layout(template="plotly_dark", paper_bgcolor="#050A14", plot_bgcolor="#050A14",
                      title=dict(text="NSE Sector Performance (Today)", font=dict(color="#E8EDF5", size=13)),
                      margin=dict(l=10,r=10,t=40,b=80), height=320, showlegend=False,
                      yaxis=dict(ticksuffix="%", gridcolor="#1A2540", zerolinecolor="#475569"),
                      xaxis=dict(tickangle=-20, gridcolor="#1A2540"))
    fig.add_hline(y=0, line=dict(color="#475569", width=1))
    return fig
