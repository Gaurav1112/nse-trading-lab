# A/B Replay Verdict — Phase 2A: rs_vs_nifty

Window: 2024-01-01 → 2024-12-31, stride=5, min_score=65.0

| Metric | v1 (baseline) | v2 (rs_vs_nifty) | Δ |
|---|---|---|---|
| Trades | 146 | 171 | +25 |
| Win rate | 45.9% | 49.1% | +3.2pp |
| Avg win | +12.61% | +12.02% | -0.59pp |
| Avg loss | -6.53% | -6.99% | -0.46pp |
| Expectancy | +2.25% | +2.35% | **+0.10pp** |
| Profit factor | 1.64 | 1.66 | +0.02 |

**Recommendation:** Ship rs_vs_nifty as the v2 default — positive expectancy delta.
