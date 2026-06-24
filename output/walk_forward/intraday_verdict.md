# Intraday walk-forward — RSI<threshold mean-reversion (15-min bars, 60-day window)

Generated 2026-06-24T08:53:20+05:30

Methodology: For each bar where RSI<threshold AND reversal-candle + volume gates pass, enter at NEXT bar's open. Exit at first of: -1% stop, +1.5% target, 15:15 IST squareoff, or end of trading day. Reproduce via `PYTHONPATH=. python3 scripts/intraday_walkforward.py`.

## Results by threshold

| RSI < | Trades | Win rate | Avg win | Avg loss | Expectancy | PF |
|---|---|---|---|---|---|---|
| 10 | 0 | — | — | — | — | — |
| 15 | 8 | 37.5% | +0.70% | -0.67% | -0.154% | 0.63 |
| 20 | 28 | 50.0% | +0.60% | -0.77% | -0.083% | 0.78 |
| 25 | 81 | 46.9% | +0.79% | -0.77% | -0.035% | 0.91 |
| 30 | 143 | 46.9% | +0.84% | -0.74% | +0.002% | 1.01 |

## How to read

- **Positive expectancy + PF > 1.0** → the strategy has measurable edge AT THIS THRESHOLD
  in the 60-day window. Run again next week to see if edge persists.
- **Negative expectancy** → mean-reversion at this RSI level is losing money net. 
  The Intraday Scanner should NOT surface BUY at this threshold for real money.
- **Very few trades (<10)** → 60-day sample too small to be meaningful; widen the window
  or thresholds.

## Honest caveats

- 60-day window is the yfinance 15-min hard limit. Real edge measurement needs 6-12 months
  of intraday data → paid feed (Kite Connect ticker ~₹2K/month).
- No transaction costs modeled here; subtract ~0.1% per round-trip for STT+brokerage+slippage.
- Universe = current Nifty 50 (survivorship bias).