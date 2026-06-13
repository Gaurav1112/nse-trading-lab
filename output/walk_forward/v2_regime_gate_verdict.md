# Walk-Forward A/B: v1 vs v2 (regime_gate + rs_vs_nifty)

Nifty 50, stride=5, min_score=65, max_hold=15. v2 engine applies both
rs_vs_nifty additive booster and regime_block defensive downgrade.

| Window | Engine | Trades | Win rate | Avg win | Avg loss | Expectancy | PF |
|---|---|---|---|---|---|---|---|
| 2023 | v1 | 103 | 75.7% | +11.63% | -5.17% | +7.55% | 7.01 |
| 2023 | v2 | 102 | 72.5% | +11.91% | -5.36% | +7.17% | 5.87 |
| 2024 | v1 | 146 | 45.9% | +12.78% | -7.48% | +1.82% | 1.45 |
| 2024 | v2 | 139 | 46.8% | +12.81% | -7.49% | +2.01% | 1.50 |
| 2025_to_today | v1 | 141 | 42.6% | +8.17% | -7.03% | -0.57% | 0.86 |
| 2025_to_today | v2 | 88 | 46.6% | +7.99% | -6.34% | +0.34% | 1.10 |

## Deltas (v2 - v1)

| Window | Δ trades | Δ win rate | Δ expectancy | Δ PF |
|---|---|---|---|---|
| 2023 | -1 | -3.2pp | -0.38pp | -1.14 |
| 2024 | -7 | +0.9pp | +0.19pp | +0.05 |
| 2025_to_today | -53 | +4.0pp | +0.90pp | +0.24 |

## Ship decision

- 2025 Δ expectancy: +0.90pp (need > +0.5pp)
- 2023 v2 expectancy: +7.17% (need > +5%)
- **VERDICT: Ship v2 (regime_gate + rs_vs_nifty) as the default engine.**

## Statistical honesty (Phase G — Tomás)

Multiple-testing tally: tested ~6 variants (rs_vs_nifty thresholds + regime_gate MIXED thresholds). Bonferroni-corrected per-test α = 0.0083 (from family α=0.05).

Expectancy 95% CI is non-parametric bootstrap (n_boot=500) on per-trade net returns. Deflated Sharpe is Bailey & Lopez de Prado (2014): probability the true Sharpe exceeds the expected maximum under N independent trials. Prob Sharpe>0 is the classic probabilistic Sharpe ratio.

| Window | Engine | Expectancy 95% CI (low / mid / high) | Deflated Sharpe | Prob Sharpe>0 |
|---|---|---|---|---|
| 2023 | v1 | +5.94% / +7.55% / +9.20% | 0.00 | 1.00 |
| 2023 | v2 | +5.42% / +7.17% / +8.78% | 0.00 | 1.00 |
| 2024 | v1 | +0.08% / +1.82% / +3.61% | 0.00 | 0.98 |
| 2024 | v2 | +0.27% / +2.01% / +4.09% | 0.00 | 0.99 |
| 2025_to_today | v1 | -2.00% / -0.57% / +0.85% | 0.00 | 0.22 |
| 2025_to_today | v2 | -1.50% / +0.34% / +1.95% | 0.00 | 0.65 |