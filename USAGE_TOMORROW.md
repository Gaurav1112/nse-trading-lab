# Using NSE Trading Lab — Tomorrow Morning

> Last updated: 2026-06-14. v2 engine is default (v3 calibrated engine also live, opt-in via `NSE_SCORER_ENGINE=v3`). Today's tape is **HOSTILE**.

## Launch (one step — pre-flight runs automatically)

```bash
cd /Users/racit/PersonalProject/nse-trading-lab
./start.sh
# Runs scripts/startup_check.py first; refuses to launch if any check fails.
# Override with SKIP_PREFLIGHT=1 ./start.sh (don't).
# Then opens http://127.0.0.1:8501 in your browser.
```

## What runs automatically (no manual action needed)

- **Daily at 18:00 IST** — GitHub Action regenerates today's tape regime
  report and commits it to `docs/daily/YYYY-MM-DD.md`. Read this file before
  opening the app if you want a quick status without launching anything.
- **Sunday 08:30 IST** — GitHub Action regenerates YTD walk-forward
  snapshots, re-runs the v1/v2 A/B verdict, retrains the calibrator, and
  commits refreshed artifacts. Pull `main` Monday morning to get them.
- **Every push** — GitHub Actions CI runs the full pytest suite. Any
  regression blocks the push from being trusted.

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
- Phase 3 v0/v1 — isotonic calibrator with walk-forward CV (engine v3, opt-in via `NSE_SCORER_ENGINE=v3`). v1 added: ceiling cap that prevents the "100% win probability" overconfidence bug, plus held-out Brier surfaced in Tape Monitor.

## Honest finding from Phase 3 v1 (read this)

The walk-forward calibrator does NOT beat a constant-base-rate predictor on held-out years. Held-out Brier:

| Fold | Isotonic | Constant baseline | Verdict |
|---|---|---|---|
| Train 2023 → eval 2024 | 0.371 | 0.248 | Worse than baseline (regime shift) |
| Train 2023+2024 → eval 2025 | 0.257 | 0.248 | Tie with baseline |
| Train all → eval 2026 partial | 0.275 | 0.231 | Worse than baseline |

Translation: **the calibrator (v3) doesn't add useful information beyond saying "the historical win rate is ~50%."** This is why v2 (with the regime gate) remains the default. The regime gate is the actual edge; calibration is honest decoration on top of it.

## Deferred (not bugs — strategic choices)

- Phase 3 v2: Regime-conditional calibration (calibrator gets regime as feature). Requires picker_replay CSVs to record regime-at-entry, then retrain. The realistic next ML improvement.
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
