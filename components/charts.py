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
                                  increasing_line_color="#10b981", decreasing_line_color="#ef4444",
                                  showlegend=False), row=1, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=e20, line=dict(color="#f59e0b", width=1), name="EMA20"), row=1, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=e50, line=dict(color="#8b5cf6", width=1), name="EMA50"), row=1, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=e200, line=dict(color="#06b6d4", width=1, dash="dot"), name="EMA200"), row=1, col=1)

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
            (score.stop_loss, f"SL ₹{score.stop_loss:.0f}", "#ef4444"),
            (score.target_1, f"T1 ₹{score.target_1:.0f}", "#10b981"),
            (score.target_2, f"T2 ₹{score.target_2:.0f}", "#6ee7b7"),
        ]:
            fig.add_hline(y=price, line=dict(color=color, width=1, dash="dash"),
                          annotation_text=label, annotation_font_color=color, row=1, col=1)

    try:
        rsi = ta_mom.RSIIndicator(close, window=14).rsi()
        fig.add_trace(go.Scatter(x=d.index, y=rsi, line=dict(color="#f59e0b", width=1),
                                  showlegend=False), row=2, col=1)
        fig.add_hline(y=70, line=dict(color="#ef4444", width=.5, dash="dot"), row=2, col=1)
        fig.add_hline(y=30, line=dict(color="#10b981", width=.5, dash="dot"), row=2, col=1)
    except Exception:
        pass

    vol_colors = ["#10b981" if c >= o else "#ef4444" for c, o in zip(d["Close"], d["Open"])]
    fig.add_trace(go.Bar(x=d.index, y=d["Volume"], marker_color=vol_colors, showlegend=False), row=3, col=1)

    fig.update_layout(template="plotly_dark", paper_bgcolor="#0a0e17", plot_bgcolor="#0a0e17",
                      title=dict(text=title, font=dict(color="#e2e8f0", size=14)),
                      margin=dict(l=10, r=10, t=40, b=10), height=720,
                      xaxis_rangeslider_visible=False, hovermode="x unified",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    fig.update_yaxes(gridcolor="#1e2a42", zerolinecolor="#1e2a42")
    fig.update_xaxes(gridcolor="#1e2a42", rangebreaks=[dict(bounds=["sat","mon"])])
    return fig


def make_equity_curve(results_list: list[dict], labels: list[str] | None = None) -> go.Figure:
    colors = ["#3b82f6","#10b981","#f59e0b","#8b5cf6","#06b6d4","#ef4444",
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
    fig.update_layout(template="plotly_dark", paper_bgcolor="#0a0e17", plot_bgcolor="#0a0e17",
                      title=dict(text="Strategy Equity Curves", font=dict(color="#e2e8f0")),
                      margin=dict(l=10,r=10,t=40,b=10), height=450, hovermode="x unified")
    fig.update_yaxes(gridcolor="#1e2a42", tickprefix="₹")
    fig.update_xaxes(gridcolor="#1e2a42")
    return fig


def make_sector_heat(sector_data: dict[str, float | None]) -> go.Figure:
    names = list(sector_data.keys())
    values = [v if v is not None else 0.0 for v in sector_data.values()]
    colors = ["#10b981" if v >= 0 else "#ef4444" for v in values]
    text = [f"{sector_data[n]:+.2f}%" if sector_data[n] is not None else "N/A" for n in names]
    fig = go.Figure(go.Bar(x=names, y=values, marker_color=colors,
                            text=text, textposition="outside",
                            textfont=dict(color="#e2e8f0", size=11)))
    fig.update_layout(template="plotly_dark", paper_bgcolor="#0a0e17", plot_bgcolor="#0a0e17",
                      title=dict(text="NSE Sector Performance (Today)", font=dict(color="#e2e8f0", size=13)),
                      margin=dict(l=10,r=10,t=40,b=80), height=320, showlegend=False,
                      yaxis=dict(ticksuffix="%", gridcolor="#1e2a42", zerolinecolor="#475569"),
                      xaxis=dict(tickangle=-20, gridcolor="#1e2a42"))
    fig.add_hline(y=0, line=dict(color="#475569", width=1))
    return fig
