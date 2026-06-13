# Using NSE Trading Lab — Tomorrow Morning

> Last updated: 2026-06-14. v2 engine is default (v3 calibrated engine also live, opt-in via `NSE_SCORER_ENGINE=v3`). Today's tape is **HOSTILE**.

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

- **Never trade against the Tape Regime banner.** HOSTILE = paper-trade only. Walk-forward under honest execution (Phase F) shows v2 2025 expectancy was +0.34% with 95% CI [-1.50%, +1.95%] — statistically indistinguishable from zero. v1 in the same window was net-negative (-0.57%).
- **Always type a thesis** (>=20 chars) before saving a position. This is mandatory friction.
- **Hard caps:** 5 concurrent positions, 5% weekly drawdown triggers a 7-day cooling-off period.
- **Check Decay Watch daily** for held positions. Sort is worst-first. Time-stop kicks in at >12 bars + re-score <50 + still underwater.

## Where your data lives

- **Trades + positions + audit log**: `~/.nse-trading-lab/` (NOT repo root anymore)
- **Calibration backtests**: `output/walk_forward/` (committed for reproducibility)
- **Snapshots**: `output/snapshots/` (committed)

## What the engine knows that you should trust

Walk-forward, v2 engine, net of Zerodha delivery costs + 0.075%/side spread + gap-through fills (Phase F honest execution):

| Regime | v2 expectancy | Win rate | Profit factor | Realistic next move |
|---|---|---|---|---|
| TRENDING (2023-like) | +7.17% per trade | 72.5% | 5.87 | Trade normally, 2% risk, take top-3 picks score ≥65 |
| MIXED (2024-like)    | +2.01% per trade | 46.8% | 1.50 | Be selective — only score ≥70 with R:R ≥2.5 |
| HOSTILE (2025-like, today) | +0.34% per trade | 46.6% | 1.10 | Paper-trade only, sit out real money |

The v2 regime gate is what keeps the engine non-negative in HOSTILE tape. Without it (v1), 2025 expectancy is **-0.57%** under honest execution.

## What's shipped (as of 2026-06-14)

- Phase 1 — silent backtest-dim bug killed, momentum decay, exits/time-stop, position_monitor, Decay Watch page
- Phase 2A/2B — rs_vs_nifty boost, tape_monitor, regime_gate (v2 default), Tape Monitor page
- Phase A–H — position persistence, required thesis (≥20 chars), risk_governor + audit log, data_freshness badges, HOSTILE empty-state UX, user-data dir
- Phase F — execution realism (gap-through fills, spread, circuit-lock)
- Phase G — deflated Sharpe + bootstrap CIs + Bonferroni
- Phase 3 v0 — isotonic calibrator (engine v3, opt-in via `NSE_SCORER_ENGINE=v3`)

## Deferred (not bugs — strategic choices)

- Phase 3 v1: Full LightGBM with proper walk-forward CV + held-out Brier score (v0 calibrator was fit in-sample-ish; clean eval needs a held-out year)
- Phase 4: Kite Connect adapter for real intraday data
- Phase 5: Real options-chain data

## Emergency stop

If something feels wrong: close Streamlit. Your positions persist on disk in `~/.nse-trading-lab/positions.json` and can be inspected with any JSON viewer. No state is ever lost in-flight.

## Recompute walk-forward expectancy quarterly

```bash
PYTHONPATH=. python3.13 scripts/walk_forward_ab.py
cat output/walk_forward/v2_regime_gate_verdict.md
```

If v2 HOSTILE-window expectancy decays below +0.2%, the regime_gate needs re-tuning urgently.
