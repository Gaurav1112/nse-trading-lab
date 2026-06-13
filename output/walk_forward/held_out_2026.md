# Held-out 2026 YTD validation — 2026-01-01 → 2026-06-14

Independent test: 2026 YTD data the engine has never been tuned
against. The calibrator was fit on 2023+2024+2025; the gates
(MTF / liquidity / gap / earnings / sector / regime) were designed
with those same years in mind. This page measures whether the
published walk-forward edge holds on unseen data.

Reproduce: `PYTHONPATH=. python3 scripts/validate_held_out_2026.py`

## Realized outcomes

| Engine | Trades | Win rate | Expectancy | Avg win | Avg loss |
|---|---|---|---|---|---|
| v1 | 175 | 35.4% | -0.962% | +4.43% | -3.92% |
| v2 | 64 | 28.1% | -1.612% | +4.60% | -4.04% |

## Interpretation guide

- **v2 expectancy similar to walk-forward 2025 estimate (+0.33%)**:
  the engine generalizes; Wave A is doing its job; HOSTILE tape remains
  in CI-includes-zero territory but the regime gate keeps it non-negative.
- **v2 expectancy materially lower**: out-of-sample degradation; the
  walk-forward A/B numbers are over-fit; size DOWN.
- **v2 expectancy materially higher**: lucky window, not edge — wait
  for more trades before scaling up.
- **Both engines very low trade count**: tape filter is doing its job
  in HOSTILE conditions; 0-low picks is the correct behaviour.

## What this is NOT

This is not a license to ignore the engine's empty-state recommendation
in HOSTILE tape. The 2025 walk-forward CI [-1.44%, +2.00%] is the
honest uncertainty; a single quarter of 2026 cannot collapse that CI.
Two quarters of paper-trading record collected by the user themselves
remains the gold-standard verification.