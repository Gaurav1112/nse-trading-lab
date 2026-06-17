# IC monitor — alpha decay diagnostic

Spearman rank correlation between each engine signal at entry and the trade's
realized net return. IC > +0.05 is considered tradeable in published quant
literature (Lopez de Prado *Advances in FML* ch. 8). Persistent IC near zero
or negative means the signal has lost (or never had) predictive power.

Reproduce: `PYTHONPATH=. python3 scripts/track_ic.py`

## IC by signal × year

| Year | n | IC(score, net%) | IC(win_prob, net%) |
|---|---|---|---|
| 2023 | 103 | +0.035 | +0.097 |
| 2024 | 146 | +0.048 | +0.074 |
| 2025 | 97 | -0.011 | -0.044 |
| 2026 | 44 | +0.025 | -0.061 |

## How to read this

- **IC > +0.10**: signal is materially predictive in this window.
- **+0.05 < IC < +0.10**: weak edge; expectancy depends on costs.
- **−0.05 < IC < +0.05**: signal carries no information; trades fire on noise.
- **IC < −0.05**: signal is contrarian — the engine is buying what loses.

## Known limitation

Current picker_replay CSVs only emit the aggregate `score` and `win_prob`
at entry, not the 6 per-dimension subscores (trend / momentum / volatility /
volume / backtest / risk). Until picker_replay is extended to dump the
dimension breakdown, this monitor cannot isolate which dimension is
decaying — it only tells you whether the aggregate score is still useful.

The per-dimension monitor is a known follow-up. To enable it: extend
`nse_backtest/picker_replay.py simulate_trade()` to record
score.trend_score / momentum_score / etc. into TradeOutcome, regenerate
snapshots, and re-run this script.