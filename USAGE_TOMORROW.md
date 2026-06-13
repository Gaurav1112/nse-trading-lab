# Using NSE Trading Lab — Tomorrow Morning

> Last updated: 2026-06-13. v2 engine is default. Today's tape is **HOSTILE**.

## Before you launch

```bash
cd /Users/racit/PersonalProject/nse-trading-lab
PYTHONPATH=. python3.13 scripts/startup_check.py
# All checks must pass. Investigate any ✗ before opening the app.
```

## Launch

```bash
./start.sh
# Opens http://localhost:8501 in your browser
```

## The order to read the screen

1. **Tape Regime banner (top of Picks).** Green = TRENDING (trade normally), amber = MIXED (be selective), red = HOSTILE (paper-trade only).
2. **Data freshness badge.** Green = fresh, amber = end-of-day, red = stale (don't trade).
3. **Risk envelope banner.** Tells you whether you're at the position limit OR in a cooling-off period.
4. **Pick cards.** Score, entry, SL, T1, R:R, win%, plus a thesis-required save form.

## The discipline contract

- **Never trade against the Tape Regime banner.** HOSTILE = paper-trade only. The walk-forward shows 2025 expectancy was +0.05% — essentially break-even after costs.
- **Always type a thesis** (>=20 chars) before saving a position. This is mandatory friction.
- **Hard caps:** 5 concurrent positions, 5% weekly drawdown triggers a 7-day cooling-off period.
- **Check Decay Watch daily** for held positions. Sort is worst-first. Time-stop kicks in at >12 bars + re-score <50 + still underwater.

## Where your data lives

- **Trades + positions + audit log**: `~/.nse-trading-lab/` (NOT repo root anymore)
- **Calibration backtests**: `output/walk_forward/` (committed for reproducibility)
- **Snapshots**: `output/snapshots/` (committed)

## What the engine knows that you should trust

| Regime | Historical expectancy | Realistic next move |
|---|---|---|
| TRENDING (2023-like) | +7.55% per trade | Trade normally, 2% risk, take top-3 picks score ≥65 |
| MIXED (2024-like)    | +2.25% per trade | Be selective — only score ≥70 with R:R ≥2.5 |
| HOSTILE (2025-like, today) | +0.05% per trade | Paper-trade only, sit out real money |

## What still needs work (deferred to next session)

- Phase F: Execution realism — bid-ask spread + circuit-limit + gap-through-SL modeling in picker_replay.
- Phase G: Deflated Sharpe + multiple-testing correction on backtest reporting.
- Phase 3: Calibrated probabilistic model (LightGBM + isotonic) trained on real walk-forward data.

## Emergency stop

If something feels wrong: close Streamlit. Your positions persist on disk in `~/.nse-trading-lab/positions.json` and can be inspected with any JSON viewer. No state is ever lost in-flight.

## Recompute walk-forward expectancy quarterly

```bash
PYTHONPATH=. python3.13 scripts/walk_forward_ab.py
cat output/walk_forward/v2_regime_gate_verdict.md
```

If 2025 expectancy decays below +0.3%, the gate may need re-tuning.
