"""
NSE Trading Lab v5.0 — Professional Trading Terminal
======================================================
Bloomberg Terminal meets TradingView — built for Indian markets.
Run: streamlit run ui.py
"""
import streamlit as st
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import os, sys, json, hashlib
from datetime import datetime, timedelta
from ta import trend, momentum, volatility, volume as ta_vol

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nse_backtest.data import fetch_nse, fetch_multiple, NIFTY50_SYMBOLS, NIFTY100_SYMBOLS
from nse_backtest.strategies import STRATEGIES
from nse_backtest.engine import run_backtest, TradeConfig
from nse_backtest.analytics import compute_metrics
from nse_backtest.scorer import analyze_stock, ScoreBreakdown
from nse_backtest.screener import run_screener
from nse_backtest.sample_data import trending_stock, volatile_midcap, sideways_stock
from nse_backtest.risk import (kelly_criterion, fractional_kelly, volatility_target_size,
                                check_drawdown_limit, position_size_risk_based,
                                calmar_ratio, compute_var_cvar, monthly_returns_table)
from nse_backtest.trading_modes import (analyze_swing, analyze_positional, analyze_longterm,
                                         analyze_intraday, analyze_options, analyze_futures,
                                         analyze_all_modes)

st.set_page_config(page_title="Trading Lab", page_icon="◆", layout="wide")

# ── Session defaults ──
DEFAULTS = {"capital": 100000.0, "risk_pct": 2.0, "sl_pct": 7.0,
            "analyze_sym": "", "auto_analyze": False, "nav_page": 0,
            "watchlist": ["RELIANCE","TCS","COALINDIA","NTPC","SBIN"],
            "journal": []}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Professional CSS ──
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=DM+Sans:wght@400;500;600;700&display=swap');
:root {
    --bg: #0a0e17; --surface: #111827; --surface2: #1a2035;
    --border: #1e2a42; --text: #e2e8f0; --muted: #64748b;
    --green: #10b981; --red: #ef4444; --blue: #3b82f6;
    --amber: #f59e0b; --cyan: #06b6d4; --purple: #8b5cf6;
    --green-bg: #052e16; --red-bg: #2a0a0a; --blue-bg: #0c1929;
}
.block-container { padding: 0.5rem 1rem; max-width: 100%; }
.stApp { font-family: 'DM Sans', sans-serif; }
h1, h2, h3 { font-family: 'DM Sans', sans-serif !important; font-weight: 700 !important; letter-spacing: -0.02em; }
code, .stCode { font-family: 'JetBrains Mono', monospace !important; }

/* Metrics */
div[data-testid="stMetric"] {
    background: var(--surface); padding: 12px 16px; border-radius: 10px;
    border: 1px solid var(--border); transition: border-color 0.2s;
}
div[data-testid="stMetric"]:hover { border-color: var(--blue); }
div[data-testid="stMetric"] label { font-size: 11px !important; color: var(--muted) !important; text-transform: uppercase; letter-spacing: 0.05em; }
div[data-testid="stMetric"] div[data-testid="stMetricValue"] { font-size: 22px !important; font-family: 'JetBrains Mono', monospace !important; }

/* Verdict badges */
.verdict-go { background: linear-gradient(135deg, #052e16 0%, #064e3b 100%); color: #6ee7b7;
    padding: 20px 24px; border-radius: 14px; text-align: center; font-size: 28px; font-weight: 700;
    border: 1px solid #10b981; box-shadow: 0 0 30px rgba(16,185,129,0.15); }
.verdict-wait { background: linear-gradient(135deg, #451a03 0%, #78350f 100%); color: #fcd34d;
    padding: 20px 24px; border-radius: 14px; text-align: center; font-size: 28px; font-weight: 700;
    border: 1px solid #f59e0b; box-shadow: 0 0 30px rgba(245,158,11,0.15); }
.verdict-avoid { background: linear-gradient(135deg, #450a0a 0%, #7f1d1d 100%); color: #fca5a5;
    padding: 20px 24px; border-radius: 14px; text-align: center; font-size: 28px; font-weight: 700;
    border: 1px solid #ef4444; box-shadow: 0 0 30px rgba(239,68,68,0.15); }

/* Cards */
.card { background: var(--surface); padding: 16px; border-radius: 10px; border-left: 3px solid var(--blue); margin: 8px 0; }
.card-g { background: var(--green-bg); padding: 16px; border-radius: 10px; border-left: 3px solid var(--green); margin: 8px 0; }
.card-r { background: var(--red-bg); padding: 16px; border-radius: 10px; border-left: 3px solid var(--red); margin: 8px 0; }
.card-y { background: #1a1a06; padding: 12px 16px; border-radius: 8px; border-left: 3px solid var(--amber); margin: 6px 0; font-size: 14px; }

/* Score gauge */
.gauge { display: inline-flex; align-items: center; justify-content: center; width: 90px; height: 90px;
    border-radius: 50%; font-family: 'JetBrains Mono', monospace; font-size: 24px; font-weight: 700; }
.gauge-go { background: conic-gradient(#10b981 var(--pct), #1e293b var(--pct)); color: #6ee7b7; }
.gauge-wait { background: conic-gradient(#f59e0b var(--pct), #1e293b var(--pct)); color: #fcd34d; }
.gauge-avoid { background: conic-gradient(#ef4444 var(--pct), #1e293b var(--pct)); color: #fca5a5; }

/* Zerodha steps */
.steps { background: var(--surface2); border: 1px solid var(--border); border-radius: 12px; padding: 18px; margin: 10px 0; }
.steps ol li { margin: 8px 0; font-size: 14px; line-height: 1.6; }

/* Tabs */
.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.stTabs [data-baseweb="tab"] { padding: 10px 20px; border-radius: 8px 8px 0 0; }

/* Sidebar */
section[data-testid="stSidebar"] { background: var(--surface); border-right: 1px solid var(--border); }

/* ══ MOBILE RESPONSIVE ══ */
@media (max-width: 768px) {
    .block-container { padding: 0.3rem 0.5rem !important; }
    h1 { font-size: 22px !important; }
    h2, h3 { font-size: 18px !important; }
    div[data-testid="stMetric"] { padding: 8px 10px; }
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] { font-size: 16px !important; }
    div[data-testid="stMetric"] label { font-size: 9px !important; }
    .verdict-go, .verdict-wait, .verdict-avoid { font-size: 20px; padding: 14px 16px; }
    .card, .card-g, .card-r { padding: 10px; font-size: 13px; }
    .steps { padding: 12px; }
    .steps ol li { font-size: 13px; }
    .gauge { width: 70px; height: 70px; font-size: 18px; }
    /* Stack columns on mobile */
    div[data-testid="column"] { min-width: 45% !important; }
    /* Bigger touch targets */
    button { min-height: 44px !important; }
    .stTextInput input { font-size: 16px !important; } /* Prevents iOS zoom */
}
@media (max-width: 480px) {
    h1 { font-size: 18px !important; }
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] { font-size: 14px !important; }
    .verdict-go, .verdict-wait, .verdict-avoid { font-size: 16px; padding: 10px 12px; }
}
</style>
""", unsafe_allow_html=True)


import re
import plotly.graph_objects as go
from plotly.subplots import make_subplots
SYMBOL_RE = re.compile(r"^[A-Z0-9&.\-^]{1,20}$")

def _validate_sym_ui(sym: str) -> str:
    """Normalize and validate a user-entered symbol; raises ValueError if bad."""
    s = (sym or "").upper().strip()
    if not SYMBOL_RE.match(s):
        raise ValueError(f"Invalid symbol: {sym!r}")
    return s

# ── Helper Functions ──
@st.cache_data(ttl=300, show_spinner=False)
def _fetch_cached(sym, start):
    """Cache yfinance data for 5 minutes to avoid re-fetching."""
    return fetch_nse(sym, start=start)

def load_data(sym, start="2020-01-01"):
    try:
        sym = _validate_sym_ui(sym)
        df = _fetch_cached(sym, start)
        if df is None or len(df) < 50:
            st.warning(f"{sym}: only {0 if df is None else len(df)} bars available — falling back to demo data.")
            return trending_stock(), True
        return df, False
    except ValueError as e:
        st.error(str(e))
        return trending_stock(), True
    except Exception as e:
        st.warning(f"Live data unavailable ({e.__class__.__name__}); using demo data.")
        return trending_stock(), True

def score_bar(label, val, tip=""):
    p = min(val / 100, 1.0)
    c = "#10b981" if p >= 0.65 else "#f59e0b" if p >= 0.45 else "#ef4444"
    t = f' title="{tip}"' if tip else ''
    return f'<div style="margin:6px 0"{t}><div style="display:flex;justify-content:space-between;font-size:11px;color:#64748b;margin-bottom:3px;text-transform:uppercase;letter-spacing:0.05em"><span>{label}</span><span style="color:{c};font-weight:600;font-family:JetBrains Mono,monospace">{val:.0f}</span></div><div style="background:#1e293b;border-radius:6px;height:6px;overflow:hidden"><div style="width:{p*100:.0f}%;background:{c};height:100%;border-radius:6px;transition:width 0.5s ease"></div></div></div>'

def score_gauge(val, verdict):
    pct = f"{val / 100 * 360:.0f}deg"
    cls = "gauge-go" if verdict == "GO" else "gauge-wait" if verdict == "WAIT" else "gauge-avoid"
    return f'<div style="display:flex;flex-direction:column;align-items:center;gap:6px"><div class="gauge {cls}" style="--pct:{pct}"><div style="background:#0a0e17;width:70px;height:70px;border-radius:50%;display:flex;align-items:center;justify-content:center">{val:.0f}</div></div><span style="font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:0.1em">{verdict}</span></div>'

def pos_calc(price, sl, cap, risk):
    d = price - sl
    if d <= 0: return 0, 0, 0
    sh = int((cap * risk / 100) / d)
    return sh, sh * price, sh * d

def make_plotly_chart(df, score=None, title="", period=250):
    """Modern interactive Plotly candlestick + RSI + Volume."""
    df_plot = df.iloc[-period:] if len(df) > period else df
    close = df_plot["Close"]
    e20 = close.ewm(span=20, adjust=False).mean()
    e50 = close.ewm(span=50, adjust=False).mean()
    rsi = momentum.RSIIndicator(close, window=14).rsi()

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        row_heights=[0.6, 0.2, 0.2],
        subplot_titles=("Price (Candles + EMA20/50)", "RSI(14)", "Volume"),
    )
    fig.add_trace(go.Candlestick(
        x=df_plot.index, open=df_plot["Open"], high=df_plot["High"],
        low=df_plot["Low"], close=close, name="Price",
        increasing_line_color="#10b981", decreasing_line_color="#ef4444",
        showlegend=False,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_plot.index, y=e20, line=dict(color="#f59e0b", width=1), name="EMA20"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_plot.index, y=e50, line=dict(color="#8b5cf6", width=1), name="EMA50"), row=1, col=1)
    if score is not None:
        fig.add_hline(y=score.stop_loss, line=dict(color="#ef4444", width=1, dash="dash"),
                      annotation_text=f"SL ₹{score.stop_loss:.0f}", row=1, col=1)
        fig.add_hline(y=score.target_1, line=dict(color="#10b981", width=1, dash="dash"),
                      annotation_text=f"T1 ₹{score.target_1:.0f}", row=1, col=1)
        fig.add_hline(y=score.target_2, line=dict(color="#10b981", width=1, dash="dot"),
                      annotation_text=f"T2 ₹{score.target_2:.0f}", row=1, col=1)
    fig.add_trace(go.Scatter(x=df_plot.index, y=rsi, line=dict(color="#f59e0b", width=1), name="RSI", showlegend=False), row=2, col=1)
    fig.add_hline(y=70, line=dict(color="#ef4444", width=0.5, dash="dot"), row=2, col=1)
    fig.add_hline(y=30, line=dict(color="#10b981", width=0.5, dash="dot"), row=2, col=1)
    vol_colors = ["#10b981" if c >= o else "#ef4444"
                  for c, o in zip(df_plot["Close"], df_plot["Open"])]
    fig.add_trace(go.Bar(x=df_plot.index, y=df_plot["Volume"], marker_color=vol_colors,
                         name="Volume", showlegend=False), row=3, col=1)
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0a0e17", plot_bgcolor="#0a0e17",
        title=dict(text=title, font=dict(color="#e2e8f0", size=14)),
        margin=dict(l=10, r=10, t=40, b=10),
        height=720,
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
        hovermode="x unified",
    )
    fig.update_yaxes(gridcolor="#1e2a42", zerolinecolor="#1e2a42")
    fig.update_xaxes(gridcolor="#1e2a42", zerolinecolor="#1e2a42",
                     rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig


def make_chart(df, score=None, title="", period=250):
    """Professional 4-panel chart: Price+BB+EMA, RSI, MACD, Volume."""
    df_plot = df.iloc[-period:] if len(df) > period else df
    close = df_plot["Close"]
    fig = plt.figure(figsize=(14, 9), facecolor='#0a0e17')
    gs = gridspec.GridSpec(4, 1, height_ratios=[3, 1, 1, 0.7], hspace=0.04)

    # Price
    ax1 = fig.add_subplot(gs[0]); ax1.set_facecolor('#0a0e17')
    ax1.plot(df_plot.index, close, color="#3b82f6", lw=1.3, label="Price")
    e20 = close.ewm(span=20).mean(); e50 = close.ewm(span=50).mean()
    ax1.plot(df_plot.index, e20, color="#f59e0b", lw=0.7, alpha=0.8, label="EMA 20")
    ax1.plot(df_plot.index, e50, color="#8b5cf6", lw=0.7, alpha=0.8, label="EMA 50")
    bb = volatility.BollingerBands(close, window=20, window_dev=2)
    ax1.fill_between(df_plot.index, bb.bollinger_hband(), bb.bollinger_lband(), alpha=0.06, color="#3b82f6")
    ax1.plot(df_plot.index, bb.bollinger_hband(), color="#3b82f6", lw=0.4, alpha=0.3)
    ax1.plot(df_plot.index, bb.bollinger_lband(), color="#3b82f6", lw=0.4, alpha=0.3)
    if score:
        ax1.axhline(score.stop_loss, color="#ef4444", ls="--", lw=0.8, alpha=0.7, label=f"SL ₹{score.stop_loss:.0f}")
        ax1.axhline(score.target_1, color="#10b981", ls="--", lw=0.8, alpha=0.7, label=f"T1 ₹{score.target_1:.0f}")
        ax1.axhline(score.target_2, color="#10b981", ls=":", lw=0.5, alpha=0.4)
    ax1.set_ylabel("₹", color="#64748b", fontsize=9)
    ax1.set_title(title, color="#e2e8f0", fontsize=12, fontweight="bold", loc="left", pad=8)
    ax1.legend(loc="upper left", fontsize=6, facecolor="#111827", edgecolor="#1e2a42", labelcolor="#94a3b8")
    ax1.tick_params(colors="#64748b", labelsize=7); ax1.grid(True, alpha=0.06, color="#1e2a42"); ax1.set_xticklabels([])
    # Current price label
    cp = close.iloc[-1]
    ax1.annotate(f"₹{cp:,.0f}", xy=(df_plot.index[-1], cp), fontsize=8, color="#3b82f6",
                 fontweight="bold", fontfamily="JetBrains Mono")

    # RSI
    ax2 = fig.add_subplot(gs[1], sharex=ax1); ax2.set_facecolor('#0a0e17')
    rsi = momentum.RSIIndicator(close, window=14).rsi()
    ax2.plot(df_plot.index, rsi, color="#f59e0b", lw=0.9)
    ax2.axhline(70, color="#ef4444", lw=0.4, ls="--", alpha=0.5); ax2.axhline(30, color="#10b981", lw=0.4, ls="--", alpha=0.5)
    ax2.fill_between(df_plot.index, 70, 100, alpha=0.04, color="#ef4444")
    ax2.fill_between(df_plot.index, 0, 30, alpha=0.04, color="#10b981")
    ax2.set_ylabel("RSI", color="#64748b", fontsize=8); ax2.set_ylim(0, 100)
    ax2.tick_params(colors="#64748b", labelsize=6); ax2.grid(True, alpha=0.06, color="#1e2a42"); ax2.set_xticklabels([])
    if len(rsi.dropna()) > 0:
        rv = rsi.iloc[-1]; rc = "#ef4444" if rv > 70 else "#10b981" if rv < 30 else "#f59e0b"
        ax2.annotate(f"{rv:.0f}", xy=(df_plot.index[-1], rv), fontsize=7, color=rc, fontweight="bold")

    # MACD
    ax3 = fig.add_subplot(gs[2], sharex=ax1); ax3.set_facecolor('#0a0e17')
    mi = trend.MACD(close, 12, 26, 9); ml = mi.macd(); sl_ = mi.macd_signal(); h = mi.macd_diff()
    ax3.plot(df_plot.index, ml, color="#3b82f6", lw=0.8); ax3.plot(df_plot.index, sl_, color="#ef4444", lw=0.6)
    ch = ["#10b981" if v >= 0 else "#ef4444" for v in h.fillna(0)]
    ax3.bar(df_plot.index, h, color=ch, alpha=0.4, width=1); ax3.axhline(0, color="#1e2a42", lw=0.3)
    ax3.set_ylabel("MACD", color="#64748b", fontsize=8)
    ax3.tick_params(colors="#64748b", labelsize=6); ax3.grid(True, alpha=0.06, color="#1e2a42"); ax3.set_xticklabels([])

    # Volume
    ax4 = fig.add_subplot(gs[3], sharex=ax1); ax4.set_facecolor('#0a0e17')
    vc = ["#10b981" if close.iloc[i] >= close.iloc[max(0, i - 1)] else "#ef4444" for i in range(len(close))]
    ax4.bar(df_plot.index, df_plot["Volume"], color=vc, alpha=0.35, width=1)
    ax4.plot(df_plot.index, df_plot["Volume"].rolling(20).mean(), color="#f59e0b", lw=0.5, alpha=0.7)
    ax4.set_ylabel("Vol", color="#64748b", fontsize=8)
    ax4.tick_params(colors="#64748b", labelsize=6); ax4.grid(True, alpha=0.06, color="#1e2a42")

    for ax in [ax1, ax2, ax3, ax4]:
        for sp in ax.spines.values(): sp.set_color("#1e2a42")
    plt.tight_layout(); return fig

def explain_trade(score):
    why, how, risk = [], [], []
    if score.trend_score >= 60: why.append("Strong uptrend — price above key EMAs, higher highs forming")
    elif score.trend_score >= 40: why.append("Mild uptrend developing — not fully confirmed yet")
    else: why.append("No clear trend — choppy price action")
    if score.momentum_score >= 60: why.append("Momentum building — RSI and MACD both confirm buying pressure")
    elif score.momentum_score >= 40: why.append("Moderate momentum — needs more confirmation")
    else: why.append("Weak momentum — buyers not in control")
    if score.volume_score >= 50: why.append("Volume confirms — institutional buying detected via OBV")
    else: why.append("Low volume — move lacks conviction")
    if score.backtest_score >= 60: how.append(f"Historically, strategies work well on this stock")
    else: how.append("Limited historical strategy success on this stock")
    if score.risk_reward >= 2: how.append(f"Risk/Reward {score.risk_reward:.1f}:1 — risking ₹1 to make ₹{score.risk_reward:.1f}")
    else: how.append(f"R:R is {score.risk_reward:.1f}:1 — ideally want ≥ 2:1")
    risk.append(f"SL at ₹{score.stop_loss:.0f} = {(score.current_price - score.stop_loss) / score.current_price * 100:.1f}% downside risk")
    if score.trend_score < 40: risk.append("No strong trend — breakout could fail and reverse")
    if score.momentum_score < 30: risk.append("Weak momentum — stock could drift sideways")
    if score.volume_score < 30: risk.append("Low volume — institutions not backing this move")
    return why, how, risk

def zerodha_steps(sym, entry, sl, shares):
    return f"""<div class="steps"><b>📱 Execute on Zerodha Kite</b>
<ol><li>Open Kite → Search <b>{sym}</b></li>
<li>Tap <b>B</b> → Product: <b>CNC</b> → Type: <b>LIMIT</b></li>
<li>Price: <b>₹{entry:,.0f}</b> | Qty: <b>{shares}</b></li>
<li>After fill → Place <b>SL-M</b> sell order at <b>₹{sl:,.0f}</b></li></ol>
<span style="color:#f59e0b;font-size:13px">⚡ Always set SL-M immediately after buying</span></div>"""


# ── Navigation ──
PAGES = ["◆ Dashboard", "🔍 Analyze", "📈 Trading Modes", "📊 Screener", "🧪 Backtest",
         "💼 Positions", "🛡️ Risk Lab", "📋 Journal", "📚 Learn", "⚙️ Settings"]

with st.sidebar:
    st.markdown('<div style="padding:8px 0;font-size:20px;font-weight:700;letter-spacing:-0.02em">◆ Trading Lab</div>', unsafe_allow_html=True)
    page = st.radio("", PAGES, index=st.session_state.nav_page, label_visibility="collapsed", key="nav")
    st.session_state.nav_page = PAGES.index(page)
    st.markdown("---")
    # Quick watchlist
    st.markdown('<span style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.1em">Watchlist</span>', unsafe_allow_html=True)
    for sym in st.session_state.watchlist[:5]:
        if st.button(f"  {sym}", key=f"wl_{sym}", use_container_width=True):
            st.session_state.analyze_sym = sym; st.session_state.auto_analyze = True
            st.session_state.nav_page = 1; st.rerun()
    st.markdown("---")
    st.caption(f"💰 ₹{st.session_state.capital:,.0f}")
    st.caption(f"🎯 {st.session_state.risk_pct}% risk = ₹{st.session_state.capital * st.session_state.risk_pct / 100:,.0f}/trade")


# ════════════════════════════════════════════════════════════════
# DASHBOARD
# ════════════════════════════════════════════════════════════════
if page == "◆ Dashboard":
    st.markdown("# ◆ NSE Trading Lab")
    st.markdown("##### Your personal market intelligence terminal")
    st.markdown("---")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Capital", f"₹{st.session_state.capital:,.0f}")
    c2.metric("Max Risk/Trade", f"₹{st.session_state.capital * st.session_state.risk_pct / 100:,.0f}")
    c3.metric("Strategies", f"{len(STRATEGIES)}")
    c4.metric("Universe", f"{len(NIFTY100_SYMBOLS)}")
    c5.metric("Scan Types", "5")

    st.markdown("---")
    dc1, dc2 = st.columns([2, 1])
    with dc1:
        st.markdown("### Quick Actions")
        st.markdown("""
| I want to... | Go to |
|---|---|
| Check if a stock is worth buying | **🔍 Analyze** — enter any symbol |
| Find the best trades today | **📊 Screener** — scan Nifty 100 |
| Test a strategy on historical data | **🧪 Backtest** — run all strategies |
| Track my open positions & MTF | **💼 Positions** — P&L + interest burn |
| Calculate how many shares to buy | **🛡️ Risk Lab** — position calculator |
| Record & review my trades | **📋 Journal** — trade diary |
| Learn trading from scratch | **📚 Learn** — complete education |
        """)
    with dc2:
        st.markdown("### Scoring System")
        st.markdown("""
        Every stock scored **0-100**:

        **≥ 65 = GO** ✅
        *Strong setup, trade it*

        **45-64 = WAIT** ⏳
        *Watch, don't enter yet*

        **< 45 = AVOID** 🚫
        *Stay away*
        """)

    st.markdown("---")
    st.markdown("### 📊 Score Dimensions")
    dims = st.columns(6)
    labels = [("TREND", "25%", "EMA stack, ADX"), ("MOMENTUM", "20%", "RSI, MACD"),
              ("VOLUME", "15%", "OBV, spikes"), ("VOLATILITY", "10%", "ATR, BB"),
              ("BACKTEST", "15%", "Strategy P&L"), ("RISK", "15%", "R:R ratio")]
    for col, (name, weight, desc) in zip(dims, labels):
        with col:
            st.markdown(f"**{name}**")
            st.caption(f"{weight} — {desc}")


# ════════════════════════════════════════════════════════════════
# ANALYZE
# ════════════════════════════════════════════════════════════════
elif page == "🔍 Analyze":
    auto_run = st.session_state.get("auto_analyze", False)
    if auto_run: st.session_state.auto_analyze = False

    st.markdown("# 🔍 Stock Analysis")
    ac1, ac2, ac3, ac4 = st.columns([3, 1, 1, 1])
    with ac1: sym = st.text_input("Symbol", value=st.session_state.get("analyze_sym", "") or "COALINDIA").upper().strip()
    with ac2: rbt = st.checkbox("Backtest", True)
    with ac3: demo = st.checkbox("Demo", False)
    with ac4:
        if sym and sym not in st.session_state.watchlist:
            if st.button("+ Watchlist"): st.session_state.watchlist.append(sym); st.rerun()

    if (st.button("🔍  Analyze", type="primary", use_container_width=True) or auto_run) and sym:
        try:
            with st.spinner(f"Analyzing {sym}..."):
                df, is_demo = (trending_stock(), True) if demo else load_data(sym)
                if is_demo and not demo:
                    st.warning(f"⚠️ Could not fetch {sym}. Using demo data — check symbol or internet.")
                score = analyze_stock(df, sym, run_backtests=rbt)
        except Exception as e:
            st.error(f"Analysis failed: {e}. Try Demo mode or check the symbol.")
            st.stop()

        # ── Verdict + Gauge ──
        vc1, vc2 = st.columns([4, 1])
        with vc1:
            cls = "verdict-go" if score.verdict == "GO" else "verdict-wait" if score.verdict == "WAIT" else "verdict-avoid"
            icon = "✅" if score.verdict == "GO" else "⏳" if score.verdict == "WAIT" else "🚫"
            label = "GO — Trade it" if score.verdict == "GO" else "WAIT — Watch" if score.verdict == "WAIT" else "AVOID — Skip"
            st.markdown(f'<div class="{cls}">{icon} {sym} — {label}<br><span style="font-size:14px;opacity:0.7">Score {score.final_score:.0f}/100 | {score.confidence} confidence | Win Prob: {score.win_probability:.0f}%</span></div>', unsafe_allow_html=True)
        with vc2:
            st.markdown(score_gauge(score.final_score, score.verdict), unsafe_allow_html=True)

        # ── Probability & Regime Strip ──
        pr = st.columns(5)
        prob_color = "#10b981" if score.win_probability >= 55 else "#f59e0b" if score.win_probability >= 40 else "#ef4444"
        pr[0].markdown(f'<div style="text-align:center;padding:8px;background:#111827;border-radius:8px;border:1px solid {prob_color}"><span style="font-size:11px;color:#64748b;text-transform:uppercase">Win Prob</span><br><span style="font-size:22px;font-weight:700;color:{prob_color};font-family:JetBrains Mono,monospace">{score.win_probability:.0f}%</span></div>', unsafe_allow_html=True)
        ev_color = "#10b981" if score.expected_value_pct > 0 else "#ef4444"
        pr[1].markdown(f'<div style="text-align:center;padding:8px;background:#111827;border-radius:8px"><span style="font-size:11px;color:#64748b;text-transform:uppercase">Expected Value</span><br><span style="font-size:22px;font-weight:700;color:{ev_color};font-family:JetBrains Mono,monospace">{score.expected_value_pct:+.1f}%</span></div>', unsafe_allow_html=True)
        pr[2].markdown(f'<div style="text-align:center;padding:8px;background:#111827;border-radius:8px"><span style="font-size:11px;color:#64748b;text-transform:uppercase">Exp. Gain</span><br><span style="font-size:22px;font-weight:700;color:#10b981;font-family:JetBrains Mono,monospace">+{score.expected_gain_pct:.1f}%</span></div>', unsafe_allow_html=True)
        pr[3].markdown(f'<div style="text-align:center;padding:8px;background:#111827;border-radius:8px"><span style="font-size:11px;color:#64748b;text-transform:uppercase">Exp. Loss</span><br><span style="font-size:22px;font-weight:700;color:#ef4444;font-family:JetBrains Mono,monospace">-{score.expected_loss_pct:.1f}%</span></div>', unsafe_allow_html=True)
        regime_icon = {"TRENDING_UP":"🟢","TRENDING_DOWN":"🔴","RANGING":"🟡","VOLATILE":"🟠"}.get(score.regime,"⚪")
        pr[4].markdown(f'<div style="text-align:center;padding:8px;background:#111827;border-radius:8px"><span style="font-size:11px;color:#64748b;text-transform:uppercase">Regime</span><br><span style="font-size:18px;font-weight:700;color:#e2e8f0">{regime_icon} {score.regime}</span></div>', unsafe_allow_html=True)

        # ── Advanced Indicators Strip ──
        if score.ichimoku_signal or score.sar_signal:
            ai1, ai2, ai3 = st.columns(3)
            if score.ichimoku_signal:
                ic_c = "#10b981" if "BULL" in score.ichimoku_signal else "#ef4444" if "BEAR" in score.ichimoku_signal else "#f59e0b"
                ai1.markdown(f'<div class="card" style="border-left-color:{ic_c}">☁️ <b>Ichimoku:</b> {score.ichimoku_signal}</div>', unsafe_allow_html=True)
            if score.sar_signal:
                sr_c = "#10b981" if "BULL" in score.sar_signal else "#ef4444"
                ai2.markdown(f'<div class="card" style="border-left-color:{sr_c}">📍 <b>SAR:</b> {score.sar_signal}</div>', unsafe_allow_html=True)

        # ── WHY / HOW / RISK ──
        why, how, risks = explain_trade(score)
        st.markdown("")
        ec1, ec2, ec3 = st.columns(3)
        with ec1:
            st.markdown("##### 🤔 Why this signal")
            for w in why: st.markdown(f"<span style='font-size:14px'>{w}</span>", unsafe_allow_html=True)
        with ec2:
            st.markdown("##### 📋 How it works")
            for h in how: st.markdown(f"<span style='font-size:14px'>{h}</span>", unsafe_allow_html=True)
        with ec3:
            st.markdown("##### ⚠️ Risks")
            for r in risks: st.markdown(f"<span style='font-size:14px'>{r}</span>", unsafe_allow_html=True)

        # ── Trade Plan ──
        st.markdown("---")
        st.markdown("### Trade Plan")
        sl_p = (score.current_price - score.stop_loss) / score.current_price * 100
        t1_p = (score.target_1 - score.current_price) / score.current_price * 100
        shares, pos, loss = pos_calc(score.current_price, score.stop_loss, st.session_state.capital, st.session_state.risk_pct)

        tc = st.columns(7)
        tc[0].metric("Price", f"₹{score.current_price:,.2f}")
        tc[1].metric("Entry", score.entry_zone)
        tc[2].metric("Stop Loss", f"₹{score.stop_loss:,.0f}", delta=f"-{sl_p:.1f}%", delta_color="inverse")
        tc[3].metric("Target 1", f"₹{score.target_1:,.0f}", delta=f"+{t1_p:.1f}%")
        tc[4].metric("Target 2", f"₹{score.target_2:,.0f}")
        tc[5].metric("R : R", f"{score.risk_reward:.1f} : 1")
        tc[6].metric("Quantity", f"{shares}" if shares > 0 else "—")

        if shares > 0:
            st.markdown(f'<div class="card">💰 <b>{shares}</b> shares × ₹{score.current_price:,.0f} = <b>₹{pos:,.0f}</b> &nbsp;|&nbsp; <span style="color:#ef4444">Max loss: ₹{loss:,.0f}</span></div>', unsafe_allow_html=True)
            st.markdown(zerodha_steps(sym, score.current_price, score.stop_loss, shares), unsafe_allow_html=True)

        if score.warnings:
            for w in score.warnings:
                st.markdown(f'<div class="card-y">⚠️ {w}</div>', unsafe_allow_html=True)

        # ── Score Breakdown ──
        st.markdown("---")
        st.markdown("### Score Breakdown")
        bc1, bc2 = st.columns(2)
        with bc1:
            st.markdown(score_bar("TREND", score.trend_score, "EMA alignment, ADX, slope"), unsafe_allow_html=True)
            st.markdown(score_bar("MOMENTUM", score.momentum_score, "RSI, MACD, StochRSI"), unsafe_allow_html=True)
            st.markdown(score_bar("VOLATILITY", score.volatility_score, "ATR, Bollinger width"), unsafe_allow_html=True)
        with bc2:
            st.markdown(score_bar("VOLUME", score.volume_score, "OBV, volume spikes"), unsafe_allow_html=True)
            st.markdown(score_bar("BACKTEST", score.backtest_score, "Strategy profitability"), unsafe_allow_html=True)
            st.markdown(score_bar("RISK", score.risk_score, "Risk/reward, distance from highs"), unsafe_allow_html=True)

        # ── Chart ──
        st.markdown("---")
        st.markdown("### Technical Charts")
        chart_period = st.select_slider("Period", [60, 120, 250, 500, 1000], value=250, format_func=lambda x: f"{x}d", key=f"chart_period_{sym}")
        try:
            pfig = make_plotly_chart(df, score, sym, period=chart_period)
            st.plotly_chart(pfig, use_container_width=True, theme=None)
        except Exception as _e:
            st.warning(f"Interactive chart failed ({_e}); falling back to static.")
            fig = make_chart(df, score, sym, period=chart_period)
            st.pyplot(fig); plt.close()

        # ── Key Technicals ──
        close = df["Close"]
        rsi_val = momentum.RSIIndicator(close, 14).rsi().iloc[-1]
        macd_h = trend.MACD(close, 12, 26, 9).macd_diff().iloc[-1]
        atr_val = volatility.AverageTrueRange(df["High"], df["Low"], close, 14).average_true_range().iloc[-1]
        e20 = close.ewm(span=20).mean().iloc[-1]; e50 = close.ewm(span=50).mean().iloc[-1]
        vol_ratio = df["Volume"].iloc[-1] / df["Volume"].rolling(20).mean().iloc[-1]

        st.markdown("### Key Numbers")
        kc = st.columns(6)
        kc[0].metric("RSI", f"{rsi_val:.1f}", delta="Overbought" if rsi_val > 70 else "Oversold" if rsi_val < 30 else "")
        kc[1].metric("MACD Hist", f"{macd_h:.2f}", delta="Bullish" if macd_h > 0 else "Bearish")
        kc[2].metric("ATR", f"₹{atr_val:.2f}")
        kc[3].metric("20 EMA", f"₹{e20:,.0f}", delta="Above" if close.iloc[-1] > e20 else "Below")
        kc[4].metric("50 EMA", f"₹{e50:,.0f}", delta="Above" if close.iloc[-1] > e50 else "Below")
        kc[5].metric("Volume", f"{vol_ratio:.1f}x avg")

        with st.expander("📋 All Signals"):
            for r in score.reasons: st.markdown(f"• {r}")

        if rbt:
            st.markdown("---")
            st.markdown("### Strategy Results")
            cfg = TradeConfig(initial_capital=st.session_state.capital, stop_loss_pct=st.session_state.sl_pct / 100)
            rows = []
            for n, f in STRATEGIES.items():
                try:
                    sd = f(df); r = run_backtest(sd, cfg); m = compute_metrics(r)
                    rows.append({"Strategy": sd["strategy_name"].iloc[-1], "Return": f"{m['total_return_pct']:.1f}%",
                                 "Sharpe": round(m['sharpe_ratio'], 2), "Max DD": f"{m['max_drawdown_pct']:.1f}%",
                                 "Win Rate": f"{m['win_rate_pct']:.0f}%", "Trades": m['total_trades']})
                except Exception as _e:
                    st.warning(f"Strategy {n} failed: {_e}")
            if rows: st.dataframe(pd.DataFrame(rows).sort_values("Sharpe", ascending=False), use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════════════
# TRADING MODES — Swing, Positional, Long-term, Intraday, Options, Futures
# ════════════════════════════════════════════════════════════════
elif page == "📈 Trading Modes":
    st.markdown("# 📈 Multi-Mode Analysis")
    st.markdown("_One stock, 5 trading perspectives — swing to options._")

    tm1, tm2 = st.columns([3, 1])
    with tm1: tm_sym = st.text_input("Symbol", "COALINDIA", key="tm_sym").upper().strip()
    with tm2: tm_demo = st.checkbox("Demo", False, key="tm_demo")

    if st.button("⚡  Analyze All Modes", type="primary", use_container_width=True) and tm_sym:
        with st.spinner(f"Running 5 analysis modes on {tm_sym}..."):
            tm_df = trending_stock() if tm_demo else load_data(tm_sym)[0]
            results = analyze_all_modes(tm_df, tm_sym, st.session_state.capital)

        # ── Overview Strip ──
        st.markdown("### At a Glance")
        oc = st.columns(5)
        modes_display = [
            ("🔄 Swing", results.get("swing"), "2-15d"),
            ("📊 Positional", results.get("positional"), "15-90d"),
            ("🏦 Long Term", results.get("longterm"), "90d+"),
            ("⚡ Intraday", results.get("intraday"), "Today"),
            ("📑 Options", None, ""),
        ]
        for i, (label, setup, tf) in enumerate(modes_display):
            if setup and hasattr(setup, 'signal'):
                sig = setup.signal
                sc = setup.score
                clr = "#10b981" if sig == "BUY" else "#ef4444" if sig == "SELL" else "#f59e0b"
                sig_icon = "✅" if sig == "BUY" else "🚫" if sig == "SELL" else "⏳"
                oc[i].markdown(f'<div style="text-align:center;padding:12px;background:#111827;border-radius:10px;border:1px solid {clr}"><span style="font-size:11px;color:#64748b">{label}</span><br><span style="font-size:20px;font-weight:700;color:{clr}">{sig_icon} {sig}</span><br><span style="font-size:12px;color:#94a3b8">{sc:.0f}/100 | {tf}</span></div>', unsafe_allow_html=True)
            elif label == "📑 Options":
                opts = results.get("options")
                if opts:
                    oc[i].markdown(f'<div style="text-align:center;padding:12px;background:#111827;border-radius:10px;border:1px solid #8b5cf6"><span style="font-size:11px;color:#64748b">{label}</span><br><span style="font-size:20px;font-weight:700;color:#8b5cf6">{opts.outlook}</span><br><span style="font-size:12px;color:#94a3b8">IV Rank {opts.iv_rank:.0f}%</span></div>', unsafe_allow_html=True)

        # ── Tabs for each mode ──
        tab_s, tab_p, tab_l, tab_i, tab_o, tab_f = st.tabs(["🔄 Swing", "📊 Positional", "🏦 Long Term", "⚡ Intraday", "📑 Options", "📜 Futures"])

        def show_trade_setup(setup):
            if not setup or setup.signal == "ERROR":
                st.error(f"Analysis failed: {setup.reasons[0] if setup.reasons else 'Unknown error'}")
                return
            clr = "#10b981" if setup.signal == "BUY" else "#ef4444" if setup.signal == "SELL" else "#f59e0b"
            st.markdown(f'<div style="background:#111827;padding:16px;border-radius:12px;border-left:4px solid {clr};margin:8px 0"><span style="font-size:22px;font-weight:700;color:{clr}">{setup.signal}</span> <span style="color:#64748b">| Score {setup.score:.0f}/100 | Win Prob {setup.win_probability:.0f}% | {setup.timeframe}</span></div>', unsafe_allow_html=True)
            mc = st.columns(6)
            mc[0].metric("Entry", f"₹{setup.entry_price:,.0f}")
            mc[1].metric("SL", f"₹{setup.stop_loss:,.0f}")
            mc[2].metric("T1", f"₹{setup.target_1:,.0f}")
            mc[3].metric("T2", f"₹{setup.target_2:,.0f}")
            mc[4].metric("R:R", f"{setup.risk_reward:.1f}:1")
            mc[5].metric("Qty", f"{setup.suggested_qty}")
            if setup.suggested_qty > 0:
                st.markdown(f"Position: **{setup.suggested_qty}** shares = ₹{setup.position_value:,.0f} | Max loss: ₹{setup.max_loss:,.0f}")
            for r in setup.reasons:
                st.caption(f"• {r}")
            for w in setup.warnings:
                st.markdown(f'<div class="card-y">⚠️ {w}</div>', unsafe_allow_html=True)

        with tab_s:
            st.markdown("### 🔄 Swing Trading (2-15 days)")
            st.markdown("_Momentum breakouts, mean reversion, technical signals._")
            show_trade_setup(results.get("swing"))

        with tab_p:
            st.markdown("### 📊 Positional Trading (15-90 days)")
            st.markdown("_Trend following with wider stops. Sector strength + weekly momentum._")
            show_trade_setup(results.get("positional"))

        with tab_l:
            st.markdown("### 🏦 Long-Term Investing (90+ days)")
            st.markdown("_CAGR, accumulation patterns, value analysis. 15% trailing stop._")
            show_trade_setup(results.get("longterm"))

        with tab_i:
            st.markdown("### ⚡ Intraday Trading (Same Day)")
            st.markdown("_VWAP, Opening Range Breakout, volume spikes. Use MIS on Zerodha._")
            show_trade_setup(results.get("intraday"))

        with tab_o:
            st.markdown("### 📑 Options Analysis")
            opts = results.get("options")
            if opts:
                oi1, oi2, oi3, oi4 = st.columns(4)
                oi1.metric("Spot", f"₹{opts.spot_price:,.0f}")
                oi2.metric("IV (HV proxy)", f"{opts.iv_current:.1f}%")
                oi3.metric("IV Rank", f"{opts.iv_rank:.0f}%")
                oi4.metric("IV Percentile", f"{opts.iv_percentile:.0f}%")
                mp1, mp2, mp3 = st.columns(3)
                mp1.metric("Max Pain (est)", f"₹{opts.max_pain:,.0f}")
                mp2.metric("PCR (est)", f"{opts.pcr:.2f}")
                mp3.metric("Outlook", opts.outlook)
                if opts.iv_rank > 50:
                    st.markdown(f'<div class="card-y">📊 IV Rank {opts.iv_rank:.0f}% — above average. Favor <b>selling premium</b> (credit spreads, iron condors)</div>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div class="card">📊 IV Rank {opts.iv_rank:.0f}% — below average. Options are <b>cheap</b> — favor buying (long calls/puts, straddles)</div>', unsafe_allow_html=True)
                st.markdown("### Strategy Suggestions")
                for strat in opts.strategies:
                    with st.expander(f"{'🟢' if 'Bull' in strat['name'] or 'Long Call' in strat['name'] else '🔴' if 'Bear' in strat['name'] or 'Long Put' in strat['name'] else '🟡'} {strat['name']}"):
                        st.markdown(f"**Legs:** {strat['legs']}")
                        st.markdown(f"**Thesis:** {strat['thesis']}")
                        st.markdown(f"**Max Profit:** {strat['max_profit']}")
                        st.markdown(f"**Max Loss:** {strat['max_loss']}")
                        st.markdown(f"**Breakeven:** {strat['breakeven']}")
                st.markdown('---')
                st.caption("⚠️ Options data is estimated from price/volatility. For real OI/IV, use Zerodha Sensibull or NSE Option Chain.")

        with tab_f:
            st.markdown("### 📜 Futures Analysis")
            fut = results.get("futures")
            if fut:
                fc = st.columns(4)
                fc[0].metric("Spot", f"₹{fut.spot_price:,.0f}")
                fc[1].metric("Futures (est)", f"₹{fut.futures_price:,.0f}")
                fc[2].metric("Basis", f"₹{fut.basis:,.0f} ({fut.basis_pct:.1f}%)")
                sig_color = "#10b981" if "LONG" in fut.signal else "#ef4444" if "SHORT" in fut.signal else "#f59e0b"
                fc[3].markdown(f'<div style="text-align:center;padding:12px;background:#111827;border-radius:8px;border:1px solid {sig_color}"><span style="font-size:11px;color:#64748b">OI Signal</span><br><span style="font-size:16px;font-weight:700;color:{sig_color}">{fut.signal}</span></div>', unsafe_allow_html=True)
                for r in fut.reasons:
                    st.markdown(f"• {r}")
                st.markdown("""
### Futures Signal Guide
| Price | Volume/OI | Signal | Meaning |
|---|---|---|---|
| ↑ | ↑ | LONG BUILD | Fresh buying — bullish |
| ↑ | ↓ | SHORT COVER | Shorts closing — weakly bullish |
| ↓ | ↑ | SHORT BUILD | Fresh selling — bearish |
| ↓ | ↓ | LONG UNWIND | Longs closing — weakly bearish |
                """)
                st.caption("⚠️ Futures data is estimated. For real OI/rollover data, use Zerodha Sensibull or NSE website.")


# ════════════════════════════════════════════════════════════════
# SCREENER — Multi-Mode
# ════════════════════════════════════════════════════════════════
elif page == "📊 Screener":
    st.markdown("# 📊 Multi-Mode Screener")

    sc1, sc2, sc3 = st.columns([2, 2, 1])
    with sc1:
        scan_mode = st.selectbox("Trading Mode", [
            "🔄 Swing (2-15 days)",
            "📊 Positional (15-90 days)",
            "🏦 Long Term (90+ days)",
            "⚡ Intraday (Same Day)",
            "📑 Options (IV Scan)",
        ])
    with sc2:
        univ = st.selectbox("Universe", ["Nifty 50", "Nifty 100", "Custom", "Demo"])
    with sc3:
        topn = st.number_input("Max", value=15, min_value=1, max_value=50)
    custom = ""
    if "Custom" in univ:
        custom = st.text_input("Symbols", "COALINDIA,NTPC,RELIANCE,TCS,SBIN,AUROPHARMA,HAL,BEL")

    if st.button("🔍  Scan", type="primary", use_container_width=True):
        try:
            progress = st.progress(0, "Fetching stock data...")
            if "Demo" in univ:
                sd = {"SAMPLE_UP": trending_stock(), "SAMPLE_FLAT": volatile_midcap(), "SAMPLE_DOWN": sideways_stock()}
            elif "Custom" in univ:
                sd = fetch_multiple([s.strip().upper() for s in custom.split(",") if s.strip()], start="2020-01-01")
            elif "100" in univ:
                sd = fetch_multiple(NIFTY100_SYMBOLS, start="2020-01-01")
            else:
                sd = fetch_multiple(NIFTY50_SYMBOLS, start="2020-01-01")

            if not sd:
                progress.empty()
                st.error("No data loaded. Check your internet connection.")
            else:
                mode_key = scan_mode.split("(")[0].strip()
                progress.progress(40, f"Loaded {len(sd)} stocks. Scanning in {mode_key} mode...")

                # Run the appropriate analyzer for each stock
                results = []
                total = len(sd)
                for idx, (sym, sdf) in enumerate(sd.items()):
                    if len(sdf) < 50:
                        continue
                    try:
                        if "Swing" in scan_mode:
                            setup = analyze_swing(sdf, sym, st.session_state.capital, st.session_state.risk_pct)
                        elif "Positional" in scan_mode:
                            setup = analyze_positional(sdf, sym, st.session_state.capital)
                        elif "Long Term" in scan_mode:
                            setup = analyze_longterm(sdf, sym, st.session_state.capital)
                        elif "Intraday" in scan_mode:
                            setup = analyze_intraday(sdf, sym, st.session_state.capital)
                        elif "Options" in scan_mode:
                            opts = analyze_options(sdf, sym)
                            setup = type('S', (), {"symbol": sym, "signal": opts.outlook,
                                "score": opts.iv_rank, "entry_price": opts.spot_price,
                                "stop_loss": 0, "target_1": 0, "target_2": 0,
                                "risk_reward": 0, "win_probability": 0, "timeframe": "Options",
                                "suggested_qty": 0, "max_loss": 0, "position_value": 0,
                                "reasons": [f"IV: {opts.iv_current:.1f}%", f"IV Rank: {opts.iv_rank:.0f}%",
                                            f"PCR: {opts.pcr:.2f}", f"Max Pain: ₹{opts.max_pain:,.0f}"] +
                                           [s["name"] + " — " + s["thesis"] for s in opts.strategies[:2]],
                                "warnings": [], "strategy_name": "Options scan"})()
                        else:
                            setup = analyze_swing(sdf, sym, st.session_state.capital)

                        results.append((sym, sdf, setup))
                    except Exception as e:
                        st.warning(f"Skipping {sym}: {e.__class__.__name__}: {e}")
                    progress.progress(40 + int(55 * (idx + 1) / total), f"Analyzed {idx+1}/{total}...")

                # Sort by score (highest first), filter to BUY signals
                if "Options" in scan_mode:
                    results.sort(key=lambda x: x[2].score, reverse=True)
                else:
                    results.sort(key=lambda x: x[2].score, reverse=True)

                progress.progress(100, "Done!")
                progress.empty()

                # Display results
                buy_results = [r for r in results if getattr(r[2], 'signal', '') == "BUY"] if "Options" not in scan_mode else results
                all_results = results[:topn]

                if not all_results:
                    st.info("No setups found for this mode.")
                else:
                    st.markdown(f"### {len(all_results)} Stocks Analyzed")

                    # Summary table
                    if "Options" in scan_mode:
                        csv_rows = [{"Symbol": sym, "IV Rank": f"{s.score:.0f}%", "Signal": s.signal,
                                     "Spot": f"₹{s.entry_price:,.0f}"} for sym, _, s in all_results]
                    else:
                        csv_rows = [{"Symbol": sym, "Signal": s.signal, "Score": f"{s.score:.0f}",
                                     "Win%": f"{s.win_probability:.0f}%", "Entry": f"₹{s.entry_price:,.0f}",
                                     "SL": f"₹{s.stop_loss:,.0f}", "T1": f"₹{s.target_1:,.0f}",
                                     "R:R": f"{s.risk_reward:.1f}", "Qty": s.suggested_qty}
                                    for sym, _, s in all_results]
                    st.download_button("📥 CSV", pd.DataFrame(csv_rows).to_csv(index=False),
                                       f"scan_{scan_mode.split()[0]}_{datetime.now():%Y%m%d}.csv", "text/csv")

                    for i, (sym, sdf, s) in enumerate(all_results):
                        sig = getattr(s, 'signal', '?')
                        sc_val = s.score
                        sig_icon = "✅" if sig == "BUY" else "🚫" if sig == "SELL" else "⏳" if sig == "HOLD" else "📊"
                        sig_color = "GO" if sig == "BUY" else "AVOID" if sig == "SELL" else "WAIT"

                        with st.expander(f"{sig_icon} #{i+1}  **{sym}** — {sig} | Score {sc_val:.0f} | {getattr(s, 'timeframe', scan_mode)}", expanded=(i < 3)):
                            if "Options" not in scan_mode:
                                mc = st.columns(6)
                                mc[0].metric("Score", f"{sc_val:.0f}")
                                mc[1].metric("Entry", f"₹{s.entry_price:,.0f}")
                                mc[2].metric("SL", f"₹{s.stop_loss:,.0f}")
                                mc[3].metric("Target", f"₹{s.target_1:,.0f}")
                                mc[4].metric("R:R", f"{s.risk_reward:.1f}:1")
                                mc[5].metric("Win%", f"{s.win_probability:.0f}%")
                                if s.suggested_qty > 0:
                                    st.markdown(f"Buy **{s.suggested_qty}** shares = ₹{s.position_value:,.0f} | Max loss: ₹{s.max_loss:,.0f}")
                                    st.markdown(zerodha_steps(sym, s.entry_price, s.stop_loss, s.suggested_qty), unsafe_allow_html=True)

                            for r in s.reasons[:8]:
                                st.caption(f"• {r}")
                            for w in s.warnings:
                                st.markdown(f'<div class="card-y">⚠️ {w}</div>', unsafe_allow_html=True)

                            # Click-through to full analysis
                            col_a, col_m = st.columns(2)
                            with col_a:
                                if st.button(f"🔍 Analyze {sym}", key=f"go_a_{i}"):
                                    st.session_state.analyze_sym = sym
                                    st.session_state.auto_analyze = True
                                    st.session_state.nav_page = 1; st.rerun()
                            with col_m:
                                if st.button(f"📈 All Modes {sym}", key=f"go_m_{i}"):
                                    st.session_state.analyze_sym = sym
                                    st.session_state.nav_page = 2; st.rerun()

        except Exception as e:
            st.error(f"Screener error: {e}. Try Demo mode.")


# ════════════════════════════════════════════════════════════════
# BACKTESTER
# ════════════════════════════════════════════════════════════════
elif page == "🧪 Backtest":
    st.markdown("# 🧪 Strategy Backtester")
    bc = st.columns([2, 1, 1])
    with bc[0]: bt_s = st.text_input("Symbol", "RELIANCE").upper()
    with bc[1]: bt_st = st.date_input("From", datetime(2018, 1, 1))
    with bc[2]: bt_d = st.checkbox("Demo", False, key="btd")

    if st.button("🚀  Run", type="primary", use_container_width=True):
        with st.spinner(f"Testing {len(STRATEGIES)} strategies..."):
            df = trending_stock() if bt_d else load_data(bt_s, str(bt_st))[0]
            cfg = TradeConfig(initial_capital=st.session_state.capital, stop_loss_pct=st.session_state.sl_pct / 100)
            res = []
            for n, f in STRATEGIES.items():
                try:
                    sd = f(df); r = run_backtest(sd, cfg); m = compute_metrics(r)
                    res.append((sd["strategy_name"].iloc[-1], r, m))
                except Exception as _e:
                    st.warning(f"Strategy {n} failed: {_e}")

        if res:
            ranked = sorted(res, key=lambda x: x[2]["sharpe_ratio"], reverse=True)
            rows = [{"#": i + 1, "Strategy": l, "Return": f"{m['total_return_pct']:.1f}%",
                     "Sharpe": round(m['sharpe_ratio'], 2), "Max DD": f"{m['max_drawdown_pct']:.1f}%",
                     "Win": f"{m['win_rate_pct']:.0f}%", "Trades": m['total_trades'],
                     "Final": f"₹{m['final_equity']:,.0f}"} for i, (l, _, m) in enumerate(ranked)]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            fig, ax = plt.subplots(figsize=(14, 5), facecolor='#0a0e17'); ax.set_facecolor('#0a0e17')
            colors = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444", "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#64748b", "#a855f7"]
            for i, (l, r, _) in enumerate(ranked):
                eq = r["equity_curve"]; n = (eq / cfg.initial_capital - 1) * 100
                ax.plot(eq.index, n.values, label=l, color=colors[i % len(colors)], lw=1)
            bh = ranked[0][1]["buy_hold_curve"]; bhn = (bh / cfg.initial_capital - 1) * 100
            ax.plot(bh.index, bhn.values, label="Buy & Hold", color="#374151", lw=1, ls="--")
            ax.set_ylabel("Return %", color="#64748b"); ax.tick_params(colors="#64748b")
            ax.legend(fontsize=6, facecolor="#111827", edgecolor="#1e2a42", labelcolor="#94a3b8")
            ax.grid(True, alpha=0.06, color="#1e2a42")
            for sp in ax.spines.values(): sp.set_color("#1e2a42")
            plt.tight_layout(); st.pyplot(fig); plt.close()

            # Risk metrics
            st.markdown("### Risk Profile — Best Strategy")
            bl, br, bm = ranked[0]
            eq = br["equity_curve"]; var = compute_var_cvar(eq)
            dd = check_drawdown_limit(eq); cal = calmar_ratio(bm["cagr_pct"], abs(bm["max_drawdown_pct"]))
            rm = st.columns(5)
            rm[0].metric("Sharpe", f"{bm['sharpe_ratio']:.2f}")
            rm[1].metric("Calmar", f"{cal:.2f}")
            rm[2].metric("VaR 95%", f"{var['var_95'] * 100:.2f}%")
            rm[3].metric("Max DD", f"{bm['max_drawdown_pct']:.1f}%")
            rm[4].metric("Win Rate", f"{bm['win_rate_pct']:.0f}%")

            with st.expander(f"📋 Trade Log — {bl}"):
                tl = br["trades"]
                if tl:
                    st.dataframe(pd.DataFrame([{"Entry": t.entry_date.strftime("%Y-%m-%d"),
                        "Exit": t.exit_date.strftime("%Y-%m-%d") if t.exit_date else "",
                        "In": f"₹{t.entry_price:,.0f}", "Out": f"₹{t.exit_price:,.0f}" if t.exit_price else "",
                        "P&L": f"₹{t.pnl:,.0f}", "%": f"{t.pnl_pct * 100:.1f}%",
                        "Why": t.exit_reason} for t in tl]), use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════════════
# POSITIONS
# ════════════════════════════════════════════════════════════════
elif page == "💼 Positions":
    st.markdown("# 💼 Position Tracker")
    try:
        p1, p2, p3 = st.columns(3)
        with p1: ps = st.text_input("Symbol", "ARSSBL"); psh = st.number_input("Shares", value=2202, min_value=1)
        with p2: pa = st.number_input("Avg ₹", value=513.0, min_value=0.1, format="%.2f"); pc = st.number_input("Current ₹", value=470.0, min_value=0.1, format="%.2f")
        with p3: pm = st.number_input("MTF %/yr", value=18.0, min_value=0.0); pd_ = st.number_input("Days", value=60, min_value=0)

        if st.button("📊  Analyze", type="primary", use_container_width=True):
            inv = pa * psh; cv = pc * psh; pnl = cv - inv; pnl_p = (pc / pa - 1) * 100 if pa > 0 else 0
            di = inv * (pm / 100) / 365 if pm > 0 else 0; ti = di * pd_
            be = pa + (ti / psh if psh > 0 else 0); rec = ((be - pc) / pc * 100) if pc > 0 else 0
            m = st.columns(4)
            m[0].metric("Invested", f"₹{inv:,.0f}"); m[1].metric("Current", f"₹{cv:,.0f}")
            m[2].metric("P&L", f"₹{pnl:,.0f}", delta=f"{pnl_p:+.1f}%", delta_color="normal" if pnl >= 0 else "inverse")
            m[3].metric("Breakeven", f"₹{be:,.2f}")
            if di > 0:
                st.markdown("### MTF Interest Burn")
                i1, i2, i3, i4 = st.columns(4)
                i1.metric("Daily", f"₹{di:,.0f}"); i2.metric("Monthly", f"₹{di * 30:,.0f}")
                i3.metric("Paid", f"₹{ti:,.0f}"); i4.metric("Recovery", f"{rec:.1f}%")
                proj = [{"Days": d, "Interest": f"₹{di * (pd_ + d):,.0f}",
                         "BE": f"₹{pa + di * (pd_ + d) / psh:,.2f}"} for d in [7, 14, 30, 60, 90]]
                st.dataframe(pd.DataFrame(proj), use_container_width=True, hide_index=True)
            st.markdown("### Exit Scenarios")
            lo = max(int(min(pc, pa) * 0.80), 1); hi = max(int(max(pa, pc) * 1.15), lo + 10)
            step = max(int((hi - lo) / 15), 1)
            exits = [{"Price": f"₹{ep}", "Gross": f"₹{(ep - pa) * psh:,.0f}",
                       "Net": f"₹{(ep - pa) * psh - ti:,.0f}", "": "✅" if (ep - pa) * psh - ti > 0 else "❌"}
                     for ep in range(lo, hi + step, step)]
            st.dataframe(pd.DataFrame(exits), use_container_width=True, hide_index=True)
            st.download_button("📥 Export", pd.DataFrame(exits).to_csv(index=False), f"{ps}_exit.csv", "text/csv")
    except Exception as e: st.error(f"Error: {e}")


# ════════════════════════════════════════════════════════════════
# RISK LAB
# ════════════════════════════════════════════════════════════════
elif page == "🛡️ Risk Lab":
    st.markdown("# 🛡️ Risk Lab")
    tab1, tab2, tab3 = st.tabs(["📐 Position Sizing", "🎰 Kelly Calculator", "📊 Portfolio Risk"])

    with tab1:
        st.markdown("### How many shares to buy?")
        rc = st.columns(3)
        with rc[0]: rp = st.number_input("Price ₹", value=470.0, min_value=1.0, format="%.2f"); rsl = st.number_input("SL ₹", value=440.0, min_value=0.1, format="%.2f")
        with rc[1]: rcap = st.number_input("Capital ₹", value=int(st.session_state.capital), min_value=1000, step=10000); rr = st.slider("Risk %", 0.5, 5.0, st.session_state.risk_pct, 0.5, key="rsk")
        with rc[2]: ratr = st.number_input("ATR ₹", value=15.0, min_value=0.1, format="%.2f"); rwr = st.slider("Win Rate %", 20, 80, 55, key="wr")
        if st.button("Calculate", type="primary", use_container_width=True, key="calc_pos"):
            ff = position_size_risk_based(rcap, rp, rsl, rr / 100)
            vt = volatility_target_size(rcap, rp, ratr, 0.15)
            kf = fractional_kelly(rwr / 100, 2 * (rp - rsl), rp - rsl, 0.25)
            ks = int(rcap * kf / rp) if kf > 0 else 0
            mc = st.columns(3)
            with mc[0]: st.markdown("**Fixed Fractional**"); st.metric("Shares", ff); st.metric("Position", f"₹{ff * rp:,.0f}")
            with mc[1]: st.markdown("**Vol Target**"); st.metric("Shares", vt); st.metric("Position", f"₹{vt * rp:,.0f}")
            with mc[2]: st.markdown("**Kelly**"); st.metric("Shares", ks); st.metric("Position", f"₹{ks * rp:,.0f}")

    with tab2:
        st.markdown("### Kelly Criterion — Optimal Bet Size")
        kwr = st.slider("Win Rate %", 30, 75, 55, key="k2"); kaw = st.number_input("Avg Win ₹", value=5000.0, min_value=1.0, format="%.0f")
        kal = st.number_input("Avg Loss ₹", value=3000.0, min_value=1.0, format="%.0f")
        fk = kelly_criterion(kwr / 100, kaw, kal) * 100; qk = fractional_kelly(kwr / 100, kaw, kal, 0.25) * 100
        st.metric("Full Kelly", f"{fk:.1f}%"); st.metric("Quarter Kelly ★", f"{qk:.1f}%")
        if fk <= 0: st.error("Kelly says DON'T trade — losing system")
        else: st.success(f"Risk **{qk:.1f}%** per trade = ₹{st.session_state.capital * qk / 100:,.0f}")

    with tab3:
        st.markdown("### Portfolio Risk Metrics")
        demo_df = trending_stock(); cfg = TradeConfig(initial_capital=st.session_state.capital, stop_loss_pct=st.session_state.sl_pct / 100)
        r = run_backtest(STRATEGIES["ema_filtered"](demo_df), cfg)
        eq = r["equity_curve"]; m = compute_metrics(r); var = compute_var_cvar(eq)
        cal = calmar_ratio(m["cagr_pct"], abs(m["max_drawdown_pct"]))
        rc = st.columns(4)
        rc[0].metric("Sharpe", f"{m['sharpe_ratio']:.2f}"); rc[1].metric("Calmar", f"{cal:.2f}")
        rc[2].metric("VaR 95%", f"{var['var_95'] * 100:.2f}%"); rc[3].metric("Max DD", f"{m['max_drawdown_pct']:.1f}%")
        mt = monthly_returns_table(eq)
        if len(mt) > 0:
            st.markdown("### Monthly Returns (%)")
            st.dataframe(mt.style.format("{:.1f}").background_gradient(cmap="RdYlGn", vmin=-10, vmax=10), use_container_width=True)


# ════════════════════════════════════════════════════════════════
# JOURNAL
# ════════════════════════════════════════════════════════════════
elif page == "📋 Journal":
    st.markdown("# 📋 Trade Journal")
    st.markdown("_Record every trade. Journal saves to a file next to ui.py._")

    # Persistent file path — always next to ui.py
    JOURNAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_journal.json")

    # ALWAYS try to load from file (catches fresh session, browser refresh, etc.)
    if "journal_loaded" not in st.session_state:
        try:
            if os.path.exists(JOURNAL_FILE):
                with open(JOURNAL_FILE, "r") as f:
                    loaded = json.loads(f.read())
                    if isinstance(loaded, list):
                        st.session_state.journal = loaded
                        st.session_state.journal_loaded = True
                    else:
                        st.session_state.journal_loaded = True
            else:
                st.session_state.journal_loaded = True
        except Exception as e:
            st.session_state.journal_loaded = True
            st.warning(f"Could not load journal: {e}")

    def _save_journal():
        """Write journal to disk."""
        try:
            with open(JOURNAL_FILE, "w") as f:
                f.write(json.dumps(st.session_state.journal, indent=2, default=str))
            return True, ""
        except Exception as e:
            return False, str(e)

    with st.expander("➕ Add New Trade", expanded=not bool(st.session_state.journal)):
        jc1, jc2 = st.columns(2)
        with jc1:
            j_sym = st.text_input("Stock Symbol", "", key="j_sym", placeholder="COALINDIA")
            j_dir = st.selectbox("Direction", ["LONG", "SHORT"], key="j_dir")
            j_date = st.date_input("Trade Date", key="j_d")
        with jc2:
            j_entry = st.number_input("Entry Price ₹", value=0.0, min_value=0.0, format="%.2f", key="j_e")
            j_exit = st.number_input("Exit Price ₹ (0 if open)", value=0.0, min_value=0.0, format="%.2f", key="j_x")
            j_qty = st.number_input("Quantity", value=0, min_value=0, key="j_q")
        j_mode = st.selectbox("Trading Mode", ["Swing", "Positional", "Long-term", "Intraday", "Options"], key="j_mode")
        j_reason = st.text_area("Why I took this trade", "", key="j_r", height=60)
        j_lesson = st.text_area("Lesson learned (fill after exit)", "", key="j_l", height=60)

        if st.button("💾  Save Trade", type="primary", use_container_width=True):
            if not j_sym:
                st.error("Enter a stock symbol")
            elif j_entry <= 0:
                st.error("Enter a valid entry price")
            elif j_qty <= 0:
                st.error("Enter quantity")
            else:
                pnl = (j_exit - j_entry) * j_qty * (1 if j_dir == "LONG" else -1) if j_exit > 0 else 0
                trade = {
                    "date": str(j_date), "symbol": j_sym.upper(), "dir": j_dir,
                    "mode": j_mode, "entry": j_entry, "exit": j_exit, "qty": j_qty,
                    "pnl": round(pnl, 2), "reason": j_reason, "lesson": j_lesson,
                    "status": "CLOSED" if j_exit > 0 else "OPEN"
                }
                st.session_state.journal.append(trade)
                saved, err = _save_journal()
                if saved:
                    st.success(f"✅ Saved {j_sym.upper()} — written to {JOURNAL_FILE}")
                else:
                    st.warning(f"Saved in session but file write failed: {err}")
                st.rerun()

    # Display journal
    if st.session_state.journal:
        st.markdown("---")
        # Summary metrics
        jdf = pd.DataFrame(st.session_state.journal)
        total_pnl = jdf["pnl"].sum()
        closed = jdf[jdf.get("status", "CLOSED") == "CLOSED"] if "status" in jdf.columns else jdf[jdf["exit"] > 0]
        open_trades = jdf[jdf.get("status", "CLOSED") != "CLOSED"] if "status" in jdf.columns else jdf[jdf["exit"] == 0]
        total_trades = len(jdf)
        wins = (jdf["pnl"] > 0).sum()
        losses = (jdf["pnl"] < 0).sum()

        mc = st.columns(5)
        pnl_color = "normal" if total_pnl >= 0 else "inverse"
        mc[0].metric("Total P&L", f"₹{total_pnl:,.0f}", delta=f"{'Profit' if total_pnl > 0 else 'Loss'}", delta_color=pnl_color)
        mc[1].metric("Win Rate", f"{wins/total_trades*100:.0f}%" if total_trades > 0 else "—")
        mc[2].metric("W / L", f"{wins} / {losses}")
        mc[3].metric("Open", f"{len(open_trades)}")
        mc[4].metric("Total", f"{total_trades}")

        # Table
        st.markdown("### All Trades")
        display_cols = ["date", "symbol", "dir", "entry", "exit", "qty", "pnl"]
        if "mode" in jdf.columns:
            display_cols.insert(3, "mode")
        if "status" in jdf.columns:
            display_cols.append("status")
        tdf = jdf[display_cols].copy()
        tdf["pnl"] = tdf["pnl"].apply(lambda x: f"₹{x:,.0f}")
        st.dataframe(tdf, use_container_width=True, hide_index=True)

        # By mode breakdown
        if "mode" in jdf.columns and jdf["mode"].nunique() > 1:
            st.markdown("### By Trading Mode")
            mode_stats = jdf.groupby("mode").agg(
                Trades=("pnl", "count"), PnL=("pnl", "sum"),
                Wins=("pnl", lambda x: (x > 0).sum())
            ).reset_index()
            mode_stats["Win%"] = (mode_stats["Wins"] / mode_stats["Trades"] * 100).round(0)
            mode_stats["PnL"] = mode_stats["PnL"].apply(lambda x: f"₹{x:,.0f}")
            st.dataframe(mode_stats, use_container_width=True, hide_index=True)

        # Export and clear
        ec1, ec2 = st.columns(2)
        with ec1:
            st.download_button("📥 Export CSV", jdf.to_csv(index=False), "trade_journal.csv", "text/csv")
        with ec2:
            if st.button("🗑️ Clear All Trades"):
                st.session_state.journal = []
                try:
                    os.remove(JOURNAL_FILE)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    st.warning(f"Could not delete journal file: {e}")
                st.session_state.journal_loaded = False
                st.rerun()

        st.caption(f"💾 Journal file: `{JOURNAL_FILE}`")
    else:
        st.info("No trades recorded yet. Add your first trade above.")


# ════════════════════════════════════════════════════════════════
# LEARN
# ════════════════════════════════════════════════════════════════
elif page == "📚 Learn":
    st.markdown("# 📚 Trading Education")
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["🔰 Basics", "📊 Indicators", "📈 Strategies", "🛡️ Risk", "💰 Fundamentals", "📖 Glossary"])

    with tab1:
        st.markdown("""
### What is Swing Trading?

Swing trading means holding stocks for **a few days to a few weeks** to capture a price move. You're not day trading (same day) or investing (years).

### The 5 Golden Rules

1. **Never trade without a stop-loss** — decide your max loss BEFORE buying
2. **Risk only 2% per trade** — ₹1L capital → max ₹2,000 risk per trade
3. **The trend is your friend** — buy stocks going UP, not stocks you hope will go up
4. **Volume confirms** — high volume + price up = real. Low volume = suspicious
5. **Cut losses fast, let winners run** — the hardest rule and the most important

### How to Start
- Open a demat account (Zerodha, Groww)
- Start with ₹10K-50K you can afford to lose
- Begin with Nifty 50 stocks only
- Use this platform to find setups
        """)

    with tab2:
        st.markdown("""
### RSI (Relative Strength Index)
- Scale 0-100. **Below 30** = oversold, **above 70** = overbought
- Best use: buy when RSI crosses above 30; sell when it drops below 70

### MACD
- Shows momentum direction. **Green histogram** = buying momentum, **red** = selling
- Signal: MACD line crossing above signal line = buy

### Bollinger Bands
- Three lines around price. **Tight bands** = big move coming
- Price above upper band with volume = strong bullish momentum

### EMA (Exponential Moving Average)
- **20 EMA** = short term, **50 EMA** = medium, **200 EMA** = long term
- Price above all three = strong uptrend

### ATR (Average True Range)
- Measures daily price range in ₹. Use for stop-loss: SL = Entry - 2×ATR

### Supertrend
- Very popular in India. Green = buy zone, Red = sell zone
        """)

    with tab3:
        st.markdown("""
### 1. Breakout Strategy ⭐ (Beginner)
1. Find stock in sideways consolidation
2. Wait for close above resistance with 2x+ volume
3. Buy next day. SL below breakout level
4. Target = consolidation range added above breakout

### 2. Pullback Strategy ⭐⭐
1. Confirm uptrend (above 50 EMA)
2. Wait for dip to 20 EMA or support
3. Buy when it bounces with a green candle
4. SL below support. Target = previous high

### 3. MACD + RSI Combo ⭐⭐
- Entry: MACD histogram positive AND RSI > 50
- Exit: MACD negative AND RSI < 45

### 4. Supertrend (Simplest)
- Buy when green. Sell when red. That's it.
        """)

    with tab4:
        st.markdown("""
### The 2% Rule
Never risk more than 2% per trade.
- ₹1L capital → max ₹2,000 risk → if SL is ₹30 below entry → buy 66 shares

### Position Sizing
```
Shares = (Capital × 2%) ÷ (Entry - Stop Loss)
```

### Stop-Loss Rules
1. Always use SL-M on Zerodha
2. Never move SL down
3. Move SL up to lock profit
4. ATR-based: Entry - 2×ATR

### Risk/Reward
Only take R:R ≥ 2:1. Even 40% win rate is profitable at 2:1.
        """)

    with tab5:
        st.markdown("""
### 💰 Fundamental Analysis — Quick Checklist

**For swing trading, fundamentals act as a safety net.** You don't need deep analysis, but avoid fundamentally weak stocks.

### Quick Health Check (5 minutes)
| Metric | Good Sign | Where to Find |
|---|---|---|
| **Revenue Growth** | Growing YoY | Screener.in → Profit & Loss |
| **Profit Growth** | Positive & growing | Screener.in → Profit & Loss |
| **Debt/Equity** | Below 1.0 (lower = better) | Screener.in → Balance Sheet |
| **ROE** | Above 15% | Screener.in → Ratios |
| **Promoter Holding** | Above 50%, not decreasing | Screener.in → Shareholding |
| **PE Ratio** | Compare with sector average | Screener.in |

### Red Flags — AVOID these stocks
- Promoter pledging > 20%
- Promoter selling shares
- Debt/Equity > 2
- Declining revenues for 3+ quarters
- Auditor qualifications or changes
- Related party transactions increasing

### Where to Check
- **Screener.in** — free fundamental data for all NSE stocks
- **Trendlyne.com** — PE, growth, DVM scores
- **BSE/NSE website** — official filings, shareholding

### Swing Trading + Fundamentals
1. Run the Screener → get technical setups
2. For each setup, spend 5 min on Screener.in
3. Check: Revenue growing? Profit positive? Low debt? Promoter holding steady?
4. If YES to all → take the trade with confidence
5. If ANY red flag → skip it, move to next setup
        """)

    with tab6:
        st.markdown("""
| Term | Meaning |
|---|---|
| **RSI** | Overbought/oversold indicator (0-100) |
| **MACD** | Momentum direction. Green = bullish |
| **BB** | Bollinger Bands. Tight = big move coming |
| **EMA** | Moving average (recent prices weighted more) |
| **ATR** | Average daily range in ₹ |
| **ADX** | Trend strength. >25 = strong |
| **OBV** | Tracks buying/selling via volume |
| **Sharpe** | Return per unit risk. >1 = good |
| **Drawdown** | Worst drop from peak |
| **R:R** | Risk:Reward. 2:1 = gain 2× what you risk |
| **SL-M** | Stop Loss Market (Zerodha auto-sell) |
| **CNC** | Cash & Carry (delivery order) |
| **MTF** | Margin funding (~18%/year interest) |
| **Kelly** | Math formula for optimal position size |
| **VaR** | Worst expected loss at confidence level |
| **Calmar** | CAGR ÷ Max Drawdown |
| **Nifty 50** | Top 50 Indian companies index |
| **FII/DII** | Foreign/Domestic institutional investors |
| **Support** | Price floor (buying pressure) |
| **Resistance** | Price ceiling (selling pressure) |
| **Golden Cross** | 50 EMA above 200 EMA = strong buy |
| **Death Cross** | 50 EMA below 200 EMA = strong sell |
        """)

# ════════════════════════════════════════════════════════════════
# SETTINGS
# ════════════════════════════════════════════════════════════════
elif page == "⚙️ Settings":
    st.markdown("# ⚙️ Settings")
    st.session_state.capital = float(st.number_input("Capital ₹", value=int(st.session_state.capital), step=10000, min_value=1000))
    st.session_state.risk_pct = st.slider("Risk %", 0.5, 5.0, st.session_state.risk_pct, 0.5)
    st.session_state.sl_pct = st.slider("Default SL %", 1.0, 15.0, st.session_state.sl_pct, 0.5)
    st.success(f"Max loss/trade: **₹{st.session_state.capital * st.session_state.risk_pct / 100:,.0f}**")
    st.markdown("---")
    st.markdown("### Watchlist")
    wl = st.text_input("Edit watchlist (comma separated)", ",".join(st.session_state.watchlist))
    if st.button("Update Watchlist"):
        st.session_state.watchlist = [s.strip().upper() for s in wl.split(",") if s.strip()]
        st.success(f"Watchlist: {', '.join(st.session_state.watchlist)}")
    st.markdown("---")
    st.markdown(f"**Strategies:** {len(STRATEGIES)} | **Nifty 100:** {len(NIFTY100_SYMBOLS)} stocks | **Zerodha:** ₹0 brokerage, STT 0.1% sell")

st.markdown("---")
st.caption("◆ NSE Trading Lab v5.0 | Not financial advice | Always use a stop-loss")
