import streamlit as st
from components import theme, state

st.set_page_config(page_title="Learn | Trading Lab", page_icon="📚", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()
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
