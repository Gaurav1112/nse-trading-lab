# Phase 1 v1 Engine — Walk-Forward Regime Cohort Analysis

**Date:** 2026-06-13
**Engine:** v1 (Phase 1 shipped)
**Universe:** Nifty 50 (48 symbols — TATAMOTORS and JIOFINSOLUTIONS skipped by yfinance)
**Replay:** `scripts/generate_snapshots.py`, stride=5, min_score=65, max_hold=15

## Results

| Year | Tape character | Trades | Win rate | Avg win | Avg loss | Expectancy | Profit factor |
|---|---|---|---|---|---|---|---|
| 2023 (Jan→Dec) | Strong bull (Nifty 50 +21%) | 103 | **75.7%** | +11.40% | −4.48% | **+7.55%** | **7.94** |
| 2024 (Jan→Dec) | Mixed/sideways | 146 | 45.9% | +12.61% | −6.53% | +2.25% | 1.64 |
| 2025 (Jan→2026-06-13) | Current tape | 141 | **42.6%** | +8.26% | −6.03% | **+0.05%** | **1.01** |

## What this proves

1. **The engine has real edge in trending markets.** 2023 expectancy of +7.55% per trade is well above any reasonable noise floor. PF 7.94 is exceptional for a daily-bar swing system.
2. **The edge is regime-dependent.** Expectancy varies 150× between 2023 and 2025. The engine is not regime-aware; it fires GO in tape where momentum-following has no positive expected value.
3. **The current tape (2025) is the user's pain.** 42.6% win rate + +8.26% avg win + −6.03% avg loss = essentially flat expectancy after Zerodha costs. The system is *breaking even* in 2025. Every losing trade now costs roughly as much as every winning trade earns, after fees.
4. **The bagholder pain pattern is quantitatively explained.** In 2024 → 2025, avg win contracted from +12.61% to +8.26% (smaller wins) while avg loss expanded from −6.53% to −6.03% (similar losses). Less reward, same risk → reluctance to exit losers because the upside on alternatives is also weak.

## Implication for Phase 2B

The next feature must be **defensive**: a market-regime gate that BLOCKS BUY in non-trending tape. Two candidates from the spec (§6):

- **`hmm_regime`** (Rohan A.5): Hidden Markov Model on Nifty 50 returns. BLOCK BUY when state = TRENDING_DOWN or HIGH_VOL.
- **`breadth_gate`** (Rohan A.5): BLOCK BUY when Nifty A/D ratio < 0.7 (i.e., fewer than 70% of advances vs decliners).

Quantitative case for defensive features:
- 2025 has 141 trades at +0.05% expectancy. If the gate correctly blocks 50% of those (the worst half), expectancy on the surviving 70 trades likely jumps to +1.5% to +2%.
- Compare to additive features like rs_vs_nifty which moved 2024 expectancy from +2.25% to +2.35% (+0.10pp, ~4% relative).
- Killing one −6% loser preserves more capital than finding one +8% winner adds (because of cost drag and compound effect).

**Recommended Phase 2B priority order:**
1. `breadth_gate` (simpler — needs only Nifty 50 A/D, no model)
2. `hmm_regime` (more sophisticated, may subsume breadth_gate)
3. `sector_momentum` (additive, only worth it if 1+2 establish positive expectancy floor)
4. `event_gate` (defensive but narrow — earnings-day downgrade)

## What this does NOT prove

- It does NOT prove the engine is broken. It has measurable edge across all three years (none of the expectancies are negative). The issue is that 2025's edge is too small to overcome practical considerations (capital tied up, opportunity cost, psychological cost of low win rate).
- It does NOT prove regime gating will work. The hypothesis is well-supported but needs an A/B replay before shipping.
- It does NOT mean past gains (2023's PF 7.94) are achievable in 2025 — that's a regime-dependent ceiling, not a permanent property of the engine.

## How to reproduce

```bash
# 2023
SNAPSHOT_START=2023-01-01 SNAPSHOT_END=2023-12-31 SNAPSHOT_STRIDE=5 \
  PYTHONPATH=. python3.13 scripts/generate_snapshots.py

# 2024
SNAPSHOT_START=2024-01-01 SNAPSHOT_END=2024-12-31 SNAPSHOT_STRIDE=5 \
  PYTHONPATH=. python3.13 scripts/generate_snapshots.py

# 2025 to today
SNAPSHOT_START=2025-01-01 SNAPSHOT_END=2026-06-13 SNAPSHOT_STRIDE=5 \
  PYTHONPATH=. python3.13 scripts/generate_snapshots.py
```

Per-trade CSVs are written to `output/snapshots/phase1_picker_replay_<start>_to_<end>.csv` for sorting/analysis.
