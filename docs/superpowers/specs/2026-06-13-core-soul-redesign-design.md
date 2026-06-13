# Core Soul Redesign — Design Spec

**Date:** 2026-06-13
**Author:** Kumar Gaurav (with Claude as drafting partner)
**Status:** Approved sections 1–3; awaiting user spec review before implementation planning
**Horizon:** Swing-first, sequenced to Positional → Intraday → Long-term
**Universe (Phase 1):** Nifty 50
**Capital range:** ₹1L–3L per portfolio, ₹100k default for sizing math
**Primary pain to solve:** Bagholder — held picks go sideways for weeks while better setups elsewhere are missed

---

## 1. Why this exists

The user's `nse-trading-lab` is a Streamlit-based, daily-bar NSE trading workstation built around a 6-dimension scorer that emits GO/WAIT/AVOID verdicts (`nse_backtest/scorer.py::analyze_stock`). The Picks page (`pages/1_Picks.py`) is the daily entry point — it calls `analyze_swing()` in `nse_backtest/trading_modes.py` and lists the top-N stocks.

The user reports three converging problems:

1. **Low trust:** picks don't have a measurable historical win rate. The "win probability" surfaced today is a logistic transform of the score, not a calibrated probability.
2. **Bagholder pain:** entered picks frequently go sideways for 30–40 days, trapping capital while better setups elsewhere are missed.
3. **UI/UX feels generic** — looks like a Streamlit demo, not a trading workstation.

Code exploration surfaced three concrete diagnoses that drive the design:

- **Silent bug in production path:** `analyze_swing` calls `analyze_stock(..., run_backtests=False)`. The "Backtest" dimension (15% of the final score) therefore always returns the neutral fallback (50/100) at runtime. 15% of every Picks verdict is noise.
- **No picker-replay backtest exists.** `score_backtest` backtests *strategies* on a stock; it does not replay the *picker's verdicts* against history. The user cannot answer "if I had taken every GO over the past year, what was my expectancy?"
- **Intraday is daily-bar proxied.** `analyze_intraday` admits in its own warning that it derives VWAP/ORB/gap signals from EOD data. A real intraday model requires a 1m/5m feed pipeline that doesn't yet exist.

The redesign keeps `analyze_swing(df, symbol, capital, risk_pct) → TradeSetup` as a stable seam and swaps the engine behind it across three phases.

---

## 2. The 21-expert team (8 pods)

| Pod | Members | Mandate |
|---|---|---|
| **A. Signal Research** (5) | Dr. Aarav Sharma (Lead Quant), Priya Iyer (Momentum/MR), Karthik Subramanian (Sector/RS), Neha Bhatt (Indicator Engineer), Rohan Mehta (Regime Detection) | Fix entry quality, redesign weighting, add features |
| **B. Risk & Exits** (3) | Vikram Rao (Head of Risk), Anita Desai (Position Lifecycle), Saurabh Khanna (MTF/Leverage) | Kill the bagholder; ATR stops, re-score, time-stops |
| **C. Probability & ML** (2) | Dr. Meera Nair (ML Lead), Ishaan Gupta (Feature Eng/CV) | Calibrated probability model, walk-forward validation |
| **D. Data Engineering** (2) | Arjun Patel (Reliability), Divya Reddy (Broker Integration) | yfinance hardening, Kite Connect adapter |
| **E. Backtest Infra** (2) | Sandeep Kumar (Picker-Replay Architect), Pooja Verma (Walk-forward/Deflated Sharpe) | The replay harness, walk-forward, deflated metrics |
| **F. UI/UX** (3) | Tara Joshi (Product Design), Karan Malhotra (Frontend), Sneha Pillai (UX Research) | Trading-terminal redesign |
| **G. QA / Governance** (2) | Vivek Chandra (Adversarial QA), Anjali Kapoor (Paper-Trade A/B) | Regression suite, v1-vs-v2-vs-v3 paper-trade gate |
| **H. NSE Domain** (2) | Ramesh Krishnan (SEBI/Tax), Lakshmi Narayanan (F&O) | Latest 2026 tax math, LTCG/STCG in journal, real OI data (Phase 4+) |

**Phase activation:**

- **Phase 1 (wk 1–2)** — 9 active: A.1, A.2, A.4, B.6, B.7, E.13, F.15–17, G.18
- **Phase 2 (wk 3–5)** — +6 activate: A.3, A.5, B.8, D.11, G.19, H.20
- **Phase 3 (wk 6–7)** — +3 activate: C.9, C.10, E.14
- **Phase 4+ (post-MVP)** — D.12, H.21

---

## 3. Approach C — Hybrid (chosen)

Surgical Phase 1 (wk 1–2) → feature augmentation Phase 2 (wk 3–5) → calibrated model Phase 3 (wk 6–7). Exit ramps at every phase boundary.

| Phase | Weeks | Output | Trust signal |
|---|---|---|---|
| 1 — Surgical | 1–2 | Bug fixes, momentum-decay penalty, daily re-score, time-stop, **picker-replay backtest + real historical snapshots** | "I can see what would have happened" |
| 2 — Features | 3–5 | RS-vs-Nifty, sector momentum, breadth gate, event gate, HMM regime | "Picks now know about the broader market" |
| 3 — Calibrated Model | 6–7 | LightGBM + isotonic calibration trained on Phase-1 replay data | "Win probabilities mean what they say" |
| UI/UX | 1–7 (parallel) | Trading-terminal redesign, Decay Watch page, Positions re-score badges, Picker Replay tab | "It looks like a workstation now" |

---

## 4. Architecture

### 4.1 Engine routing seam (unchanged interface)

```python
# scorer.py
ENGINE = os.getenv("NSE_SCORER_ENGINE", "v1")

def analyze_swing(df, symbol, capital, risk_pct):
    if ENGINE == "v3":
        return _v3_calibrated_swing(df, symbol, capital, risk_pct)
    elif ENGINE == "v2":
        return _v2_feature_augmented_swing(df, symbol, capital, risk_pct)
    return _v1_swing(df, symbol, capital, risk_pct)
```

All UI surfaces continue to call `analyze_swing`. Engine version is selectable via env var. v1/v2/v3 can run side-by-side for paper-trade A/B comparison.

### 4.2 New modules

```
nse_backtest/
├── picker_replay.py         # Phase 1 — walks history, emits per-trade outcomes
├── position_monitor.py      # Phase 1 — daily re-score, time-stop, decay exit
├── features/                # Phase 2
│   ├── relative_strength.py
│   ├── sector_momentum.py
│   ├── breadth.py
│   ├── event_gate.py
│   └── regime.py            # HMM-based, replaces ad-hoc detect_market_regime
└── model/                   # Phase 3
    ├── calibrated_scorer.py
    ├── training.py
    └── deflated_metrics.py
```

---

## 5. Phase 1 — Surgical fixes (wk 1–2)

### 5.1 Kill the silent `run_backtests=False` bug

`analyze_swing` currently produces a verdict where 15% of weight is a constant 50/100. Resolution (decision made together): drop the backtest dimension from swing mode and re-normalize weights:

| Dimension | Old weight | New weight |
|---|---|---|
| Trend | 0.25 | 0.30 |
| Momentum | 0.20 | 0.23 |
| Volume | 0.15 | 0.18 |
| Volatility | 0.10 | 0.12 |
| Backtest | 0.15 | (removed) |
| Risk | 0.15 | 0.17 |

Strategy-friendliness as a pre-filter (not a score dimension) returns in Phase 2 as a nightly-precomputed feature.

### 5.2 Momentum-decay penalty (Priya Iyer, A.2)

```
if ROC_5 < 0.3 × ROC_20  AND  OBV_slope_5d < 0.2 × OBV_slope_20d:
    momentum_score -= 25
    reasons.append("Momentum decaying — risk of bagholding")
```

Implementation lives in `score_momentum()` in `scorer.py`. Regression test in `tests/test_scorer.py` verifies the penalty fires only when both conditions hold.

### 5.3 Daily position re-score (Anita Desai, B.7)

`nse_backtest/position_monitor.py::daily_check(positions) -> list[ReScoreVerdict]` runs `analyze_swing` against today's data for each held position and emits one of:

- `HOLD` — re-score ≥ 60
- `TIGHTEN_STOP` — re-score 45–60; SL tightened to `max(current_SL, last_swing_low - 0.5*ATR)`
- `EXIT` — re-score < 45, or time-stop fired (see §5.4), or trail-SL hit

Surfaced as a colored badge on every row of `pages/7_Positions.py`. New page `pages/12_Decay_Watch.py` lists held positions sorted by re-score ascending (worst first).

### 5.4 Time-stop

```
if bars_held > 12  AND  current_rescore < 50  AND  current_price < entry_price:
    emit EXIT at next open
```

Configurable per mode (default 12 bars for swing, 30 for positional). Stored on `TradeSetup` and persisted in the journal so the post-mortem can attribute outcome to the exit reason.

### 5.5 Trail-SL after T1 (Vikram Rao, B.6)

When price hits T1:
1. Take 50% off the table.
2. Move SL to entry (break-even).
3. Trail the SL behind price by `max(1.5 × ATR, last_swing_low - 0.3 × ATR)` on each new daily high.

Today's engine has fixed targets and no trailing. This is the highest-leverage exit change and directly addresses the bagholder pattern after a partial win.

### 5.6 Picker-replay backtest (Sandeep Kumar, E.13)

`nse_backtest/picker_replay.py`:

```python
def replay_picker(
    symbols: list[str],
    start: date, end: date,
    min_score: float = 65,
    max_hold: int = 15,
    capital: float = 100_000,
    engine: str = "v1",
) -> BacktestReport:
    """Walks every trading day, calls analyze_swing on truncated history,
    simulates the trade plan forward without look-ahead, records outcomes."""
```

Output `BacktestReport` exposes: total trades, win rate, average gain, average loss, expectancy %, profit factor, max drawdown, equity curve, per-trade dataframe. The per-trade rows are exactly what the user-facing snapshots are built from.

UI integration: new tab in `pages/6_Backtest.py` — "Picker Replay". Pick universe + date range + engine version → see report + per-trade table + downloadable CSV.

### 5.7 Phase 1 success criteria

- 140-test regression suite still green (we already have it as of commit `7aab984`).
- New regression tests: momentum-decay fires only on decay condition; time-stop fires on all three conditions; trail-SL never lets exit price drop below entry once T1 hit.
- `replay_picker` runs Nifty 50 over 2023-01-01..2025-12-31 in <10 minutes.
- Two real (not illustrative) snapshots produced from `replay_picker` for the user.

---

## 6. Phase 2 — Feature augmentation (wk 3–5)

Each feature is a small additive booster on the Phase 1 base score, behind a feature flag, each measured by an A/B `replay_picker` run.

| Feature | Owner | Effect | Hypothesis |
|---|---|---|---|
| `rs_vs_nifty` | Karthik (A.3) | +10 if 20d AND 60d outperformance vs Nifty 50 > +5% | Outperformers persist 4–8 weeks |
| `sector_momentum` | Karthik (A.3) | +5 if stock's NSE sector index is in top-3 of 11 sectors over 20d | Sector flow lifts all boats |
| `breadth_gate` | Rohan (A.5) | BLOCK BUY if Nifty A/D < 0.7 | Don't fight thin tape |
| `event_gate` | Ramesh (H.20) | Downgrade GO→WAIT within 3 days of earnings, ex-div, F&O expiry | Earnings is a coin flip; sized down or skipped |
| `hmm_regime` | Rohan (A.5) | BLOCK BUY in TRENDING_DOWN regime; HMM replaces ad-hoc rules | More robust regime detection |
| `strategy_friendliness` | Dr. Aarav (A.1) | +5 to +10 from nightly-precomputed backtest score across all 11 strategies on the symbol (re-introduces the dimension dropped in §5.1, but as a nightly cache, not a runtime call) | The original signal had merit — Phase 1's only complaint was that it ran live and was bypassed |

Each feature ships only if its A/B replay shows positive expectancy delta with statistical significance (paired t-test on per-day cohort returns).

---

## 7. Phase 3 — Calibrated model (wk 6–7)

- **Training data:** Phase 1 replay produces ~187,500 labeled (date, symbol) candidate setups over 3 years × Nifty 50. Each row has features at decision time and outcome (win/loss, return %).
- **Features:** all 5 dimension scores + Phase 2 boosters + raw indicator values (~40 total).
- **Model:** Logistic regression baseline + LightGBM, both with isotonic calibration on a held-out window. After calibration, `win_probability=0.65` means 65% of those trades actually win on out-of-sample data.
- **Validation:** Walk-forward — train 2y, test 3 months, roll. Deflated Sharpe (Pooja, E.14) discounts for the number of feature combinations explored.
- **Rollout gate:** Anjali (G.19) paper-trades v1 vs v3 on live (delayed) data for 4 weeks. v3 only replaces v1 by default if it wins on out-of-sample expectancy at p < 0.05.

---

## 8. UI/UX (parallel through all phases)

### 8.1 Principles
- Dark-only, monospaced numerics, column-aligned tables
- Signal density: every Picks card shows score, verdict, win-prob, entry/SL/T1, sizing, top-3 reasons in one viewport
- Color is semantic: green=GO, amber=WAIT, red=AVOID, blue=HOLD-VALID, magenta=TIME-STOP-PENDING
- Top status bar on every page: market regime, data freshness, engine version

### 8.2 Page changes

| Page | Change |
|---|---|
| Picks (`pages/1_Picks.py`) | New card layout: left rail = score/verdict, grid = R:R + win-prob + sizing, expandable reasons. Kite deep-link kept. |
| Positions (`pages/7_Positions.py`) | Re-score badge per row; default sort by re-score ascending. |
| Backtest (`pages/6_Backtest.py`) | New "Picker Replay" tab driven by `picker_replay`. |
| **NEW: Decay Watch** (`pages/12_Decay_Watch.py`) | Single-purpose: "what should I exit today?" — held positions ranked by re-score. |

### 8.3 Component library cleanup (Karan, F.16)

`components/` currently has theme/charts/cards/security/state/market_data as parallel modules. Phase 1 consolidates the card library into a single `components/cards.py` with `pick_card()`, `position_card()`, `decay_card()`, `replay_trade_row()`.

---

## 9. Snapshot deliverable

The user asked: "if I had taken these picks historically, what gain would I have got?"

**Phase 1 week 2** ships two real snapshots produced by `picker_replay` against Nifty 50 2024 history. Each snapshot includes:
- Symbol, entry date, mode (SWING)
- Engine verdict + score + win-probability at entry (computed on truncated data, no look-ahead)
- Top 5 reasons the engine flagged it (from the actual scorer reason list)
- The plan: entry / SL / T1 / T2 / R:R / position sizing for ₹2L capital
- Forward outcome: trade timeline (T1 hit, trail-SL hit, time-stop, etc.)
- Final realized return gross and net of Zerodha delivery costs
- Profit in ₹ on the suggested position size
- Holding period in trading days

The full per-trade CSV is downloadable from the new Picker Replay tab — winners and losers, so expectancy is judged on the unflinching dataset.

Illustrative format (TATAMOTORS, BHARTIARTL) was shown during brainstorming for sign-off on the *shape* of the snapshot. The real Phase 1 picks may differ — they will come from whichever stocks the engine actually flagged with score ≥ 65 in early 2024.

---

## 10. Risks & exit ramps

| Risk | Mitigation | Exit ramp |
|---|---|---|
| Phase 1 fixes overfit to recent regime | Phase 1 replay covers 2023–2025 across bull, sideways, and correction phases | Roll back individual fixes via feature flag if A/B replay shows negative delta |
| Phase 2 features double-count existing signals | Each booster has its own A/B replay; ship only if expectancy delta is positive at p < 0.05 | Drop the feature; the previous engine version is preserved |
| Phase 3 model underperforms v2 | Paper-trade A/B before default flip; deflated Sharpe checks for overfit | Keep v2 as default; v3 ships only on demonstrated edge |
| yfinance data quality regression hits live picks | Arjun (D.11) adds corporate-action detection + split-adjust anomaly alarm | Live Picks page already has a >5% live-vs-cached price ratio guard (commit `80ddd9b`) |
| Bagholder pattern returns post-fixes | Decay Watch + time-stop is the structural defense; weekly review by user with QA pod | Tighten time-stop threshold from 12 → 8 bars if recurrence observed |

---

## 11. Out of scope (this spec)

- Full intraday data pipeline (Kite Connect 1m/5m bars). Captured for **Phase 4**.
- Real options chain (NSE bhavcopy / broker OI). Captured for **Phase 5**.
- Fundamentals (results-day numbers, earnings revisions). The `event_gate` in Phase 2 only knows the *date* of earnings, not the *content*. Fundamentals integration is a separate spec.
- Mobile-native UI. Streamlit-responsive is acceptable through Phase 3.

---

## 12. Timeline summary

| Week | Phase | Headline deliverable |
|---|---|---|
| 1 | Phase 1 | Silent-bug fix, momentum-decay penalty, weight renormalization, regression tests |
| 2 | Phase 1 | `picker_replay.py` shipped, **two real snapshots delivered to user**, daily re-score live, time-stop live, trail-SL live |
| 3 | Phase 2 | `rs_vs_nifty`, `sector_momentum` features behind flags + A/B reports |
| 4 | Phase 2 | `breadth_gate`, `event_gate` behind flags + A/B reports |
| 5 | Phase 2 | `hmm_regime`, v2 engine consolidated, paper-trade vs v1 begins |
| 6 | Phase 3 | Training data export, baseline logistic model, isotonic calibration |
| 7 | Phase 3 | LightGBM, walk-forward + deflated Sharpe, v3 engine consolidated |
| 4–7 | Paper trade | v1 vs v2 (wk 4–5), v1 vs v2 vs v3 (wk 6–7) — 4 cumulative weeks of live observation before v3 default switch |

Parallel UI/UX work threads through all weeks; Decay Watch ships in week 2.
