# A/B Replay Verdict — Phase 2A: rs_vs_nifty

Window: 2024-01-01 → 2024-12-31, stride=5, min_score=65.0

| Metric | v1 (baseline) | v2 (rs_vs_nifty) | Δ |
|---|---|---|---|
| Trades | 146 | 183 | +37 |
| Win rate | 45.9% | 49.2% | +3.3pp |
| Avg win | +12.61% | +11.74% | -0.86pp |
| Avg loss | -6.53% | -6.93% | -0.40pp |
| Expectancy | +2.25% | +2.26% | **+0.00pp** |
| Profit factor | 1.64 | 1.64 | +0.00 |

**Recommendation:** Ship rs_vs_nifty as the v2 default — positive expectancy delta.
