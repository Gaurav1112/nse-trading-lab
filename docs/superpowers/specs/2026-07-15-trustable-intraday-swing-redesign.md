# Trustable Intraday + Swing Redesign — Design Spec

**Date:** 2026-07-15  
**Owner:** Kumar Gaurav  
**Status:** Draft for user review before implementation-plan  
**Supersedes:** the current 17-page `nse-trading-lab` Streamlit structure

---

## 0. Problem statement

The current app has 17 pages, is Streamlit-only (nothing runs when the UI is closed), and its most honest signal — the HOSTILE tape gate — makes the app feel useless because it says "don't trade" ~60% of the year with no next action. The user (real-money swing trader, ₹1L–3L capital, Nifty 50) has stopped getting value from it. Held-out 2026 walk-forward confirmed the HOSTILE gate is correct (-1.61% expectancy in that regime) — so the fix cannot be to soften the gate. The fix must be to make the app *useful in every regime*, *trustable enough to act on blindly*, and to focus ruthlessly on intraday + swing (everything else is noise).

## 1. Goals & non-goals

### Goals (in priority order)
1. **The user opens the PWA on any trading day and sees a next action** — whether that's "buy signal fired" (TRENDING/MIXED) or "here are the 3 conditions we're watching for the regime to flip, and 5 setups getting ready" (HOSTILE).
2. **Live market pricing (Fyers API, free) visible in the UI** — hard requirement per user. Not 15-min lagged. Trust badge shows source health explicitly.
3. **Alerts fire via PWA Web Push** so the user doesn't need to open the app to hear about a signal.
4. **Every signal carries a full Univest-standard parameter set** — entry, stop-loss, target, R:R, position size, thesis, invalidation, plus a Danelfin-style "top 3 features that fired" explainability line.
5. **Trust ledger** — every signal ever generated + every paper-trade outcome + live-vs-backtest divergence chart on the landing page. This is the "blindly trustable" artifact.
6. **Loud when broken** — pipeline health, data staleness, engine drift each have their own alert channel and their own kill-switch behavior.
7. **17 pages → 6 visible + Advanced drawer** — no page is deleted, but everything not directly serving intraday or swing decisions is hidden from primary nav.

### Non-goals for v1
- Broker order placement (alerts + copy-paste to Kite; auto-order deferred to v2)
- 1-minute intraday bars (needs Kite Connect ₹500/mo + always-on VPS — deferred)
- Nifty 100 / 200 universe expansion (deferred)
- Options mode (measured retail edge is thin — deferred)
- Any AI-generated commentary (explainability comes from features + walk-forward, not from a language model)

## 2. Design principles

- **MECE**: each concern (architecture / UI / pipeline / ledger / rollout) lives in exactly one section of this spec. Each subsystem writes to exactly one output and reads from exactly one input.
- **Pareto**: Loop 1 delivers ~70% of user value in ~20% of the total build effort. Later loops have diminishing returns.
- **Debate**: every choice below cites the alternative it beat and why. Nothing chosen by fiat.
- **Optimize expectancy, not win rate**: the entire UI language is R-multiples ("+0.34R average trade") not win-rate percentages. Van Tharp / Turtle Traders convention. Prevents the "high win rate is safe" trap.
- **Honesty is a feature**: every losing paper trade is visible; the -1.61% HOSTILE finding is a permanent citation on the equity chart; no confetti, no streaks, no dopamine loops.
- **Loud when broken > silent when correct**: staleness, drift, and pipeline death each have their own explicit visible + push signal.

## 3. Architecture (three cooperating pieces)

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. SIGNAL PIPELINE (GitHub Actions, every 5 min, NSE hours)     │
│    Public repo: nse-trading-lab (unlimited free Actions minutes) │
│    ─ Fetches: Fyers live quotes (primary), yfinance bars (fallback) │
│    ─ Computes: tape regime, SWING scorer, INTRADAY RSI scanner  │
│    ─ Writes to PRIVATE signals repo via deploy key              │
│    ─ Fires PWA Web Push on new non-deduped signals              │
│    ─ Self-heartbeat + separate "pipeline dead" alerter          │
└──────────────────────────────────────────────────────────────────┘
                        │ writes
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│ 2. TRUST LEDGER (private signals repo, append-only)             │
│    ─ signals.parquet: every signal ever generated               │
│    ─ paper_trades.parquet: outcome per signal (auto-filled)     │
│    ─ equity_daily.parquet: rolled up daily P&L per mode         │
│    ─ state/latest.json: what UI reads                           │
│    ─ state/pipeline_health.json: heartbeat                      │
└──────────────────────────────────────────────────────────────────┘
                        │ reads
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│ 3. TODAY UI (Streamlit, PWA-installable via Chrome)             │
│    ─ Read-only view of the ledger                               │
│    ─ Four-cell regime cockpit (persistent)                      │
│    ─ Live-vs-backtest equity chart (Composer gap moat)          │
│    ─ Mode switch: SWING (Advisory) / INTRADAY (Scalper)         │
│    ─ Signal cards or "why we're NOT trading today" surface       │
│    ─ Rebalance diff narrative                                    │
└──────────────────────────────────────────────────────────────────┘
                        │ Web Push
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│ 4. PWA + WEB PUSH (Chrome desktop + Android + iOS 16.4+)        │
│    ─ VAPID keys + service worker                                │
│    ─ Notification body includes symbol, entry, SL, target, R:R  │
│    ─ Tap → deep-link to Kite chart for that symbol              │
│    ─ Cap: 3 alerts/mode/day (spam guard)                        │
│    ─ Fallback stubbed: if push endpoint 5xx, alert_queue.json + │
│       UI amber banner. Telegram fallback deferrable ~30 LOC.    │
└──────────────────────────────────────────────────────────────────┘
```

**Repo split (per user decision):** code repo `nse-trading-lab` stays PUBLIC (unlimited Actions minutes, code visible for portfolio). Separate PRIVATE repo `nse-trading-lab-signals` holds live signals + ledger + audit log. Pipeline in code repo pushes to signals repo via deploy key.

**Alternatives considered and rejected:**
- *Big-bang unified UI rebuild without splitting into pipeline/ledger/UI*: rejected because it keeps the "nothing runs when UI is closed" problem, which forbids alerts.
- *Everything in one private repo*: rejected because user wanted code visibility for portfolio; separate signals repo is a small extra piece.
- *Database (Postgres/SQLite) for the ledger*: rejected in favor of parquet + git — cheaper, portable, inspectable with pandas, no live-service to keep alive.

## 4. Data sources & freshness (HARD REQUIREMENT: live pricing)

| Source | Purpose | Freshness | Failure mode |
|---|---|---|---|
| **Fyers API `/quotes`** (primary LTP) | Live entry-price display on every signal card + regime cockpit | <1s when working | Alerts fire, UI shows amber "live prices degraded" banner, auto-falls-back to yfinance |
| **yfinance 15-min bars** (fallback + computation) | Signal computation (RSI, moving averages, regime) + LTP fallback | ~15 min | STALE flag on any bar >20 min old |
| **yfinance daily bars** (Nifty index) | Tape regime, walk-forward baseline | 1h cache | Fall back to yesterday's cached close |
| **NSE public JSON endpoint** (best-effort cross-check) | Divergence gate against Fyers/yfinance | 1–3 s | If NSE >0.5% off from primary, alert fires (data-quality watch) |

**Non-negotiable:** the UI must show live prices whenever Fyers is up. When Fyers is degraded, the trust badge switches to amber and the number shown is labeled `(15-min lagged, Fyers down)` explicitly. Under no circumstances does the UI show a lagged number as if it were live.

**Fyers operational tax (documented in Settings):**
- Free demat signup ~15 min (user completes once)
- API key + secret stored in GitHub Secrets (`FYERS_API_KEY`, `FYERS_SECRET`)
- Access token refresh: Fyers tokens have longer validity than Kite (verify at implementation time); if manual refresh is needed, provide a Settings-page button + a push nag if token expired at 09:15.

## 5. Today UI (screen composition)

### 5.1 Landing layout (Streamlit multi-page: `pages/0_Today.py`)

```
┌──────────────────────────────────────────────────────────────────┐
│ 🟢 Pipeline healthy · Live prices ON (Fyers) · Last 2 min ago   │  ← trust badge
├──────────────────────────────────────────────────────────────────┤
│ ╭──────────╮ ╭──────────╮ ╭──────────╮ ╭──────────╮             │
│ │ TAPE     │ │ INDIA VIX│ │ BREADTH  │ │ NIFTY vs │             │  ← 4-cell
│ │ MIXED ▲  │ │ 14.2 ▼2% │ │ 62% ▲    │ │ 200EMA   │             │    cockpit
│ │ selective│ │ risk-off │ │ 62/100 up│ │ +3% ↑    │             │    (ToS pattern)
│ ╰──────────╯ ╰──────────╯ ╰──────────╯ ╰──────────╯             │
├──────────────────────────────────────────────────────────────────┤
│ 📊 ENGINE TRUST — Live paper vs walk-forward baseline           │
│    Chart: cumulative paper P&L (green) vs backtest baseline     │
│    (blue). Divergence: -0.3% (within 1σ, ✓ engine tracking)     │
├──────────────────────────────────────────────────────────────────┤
│ ○ SWING (Advisory)  ● INTRADAY (Scalper)   ← mode switch        │
├──────────────────────────────────────────────────────────────────┤
│ 🎯 TODAY'S SIGNALS — 2 active                                    │
│ [signal cards, one per active signal — see §5.3]                 │
├──────────────────────────────────────────────────────────────────┤
│ 🔄 CHANGED SINCE YESTERDAY                                       │
│ + TCS entered watchlist (broke above 3820)                       │
│ - HDFC exited (thesis broken: closed below 200EMA)               │
│ ± INFY signal weakened (RSI drift, still holding)                │
└──────────────────────────────────────────────────────────────────┘
```

### 5.2 HOSTILE-day variant (replaces the "TODAY'S SIGNALS" block)

```
┌──────────────────────────────────────────────────────────────────┐
│ 🛑 WHY WE'RE NOT TRADING TODAY                                    │
│                                                                  │
│ Tape is HOSTILE. Walk-forward on held-out 2026 YTD returned      │
│ -1.61% expectancy per trade in this regime (64 trades, 28% wins).│
│                                                                  │
│ What has to happen before we trade again:                        │
│   □ India VIX drop below 15   (now 18.2 · ▓▓▓░░░ 40%)           │
│   □ Breadth expand above 55%  (now 41%  · ▓▓▓▓░░ 65%)           │
│   □ Nifty reclaim 200EMA      (now -1.8% below · ▓▓░░░░░ 22%)   │
│                                                                  │
│ 📋 Prep board — 5 setups getting ready:                          │
│ · TCS  distance to trigger 3820: -1.2%                           │
│ · INFY distance to trigger 1580: +0.4% (currently in trigger)    │
│ · HDFCBANK distance 1620: -3.8%                                  │
│ · ... [2 more]                                                   │
│                                                                  │
│ Alerts fire when: any prep-board setup triggers AND ≥2 of the 3  │
│ regime conditions above flip green.                              │
└──────────────────────────────────────────────────────────────────┘
```

This surface is the app's biggest UX innovation. No competitor in the Indian retail research (Univest, StockEdge, Streak, Sensibull, Tickertape, Smallcase, Trendlyne) ships anything equivalent — HOSTILE-tape competitors just show a red banner or nothing. Turning "don't trade" into "here's what we're watching" converts silence into signal.

### 5.3 Signal card (Univest schema + Danelfin explainability + MarketSmith buy-window)

```
┌────────────────────────────────────────────────────────────────┐
│ RELIANCE · BUY · MIXED tape ✓ · conf 82% · fresh 4min          │
│ ▁▂▃▄▄▅▆▇█  spark chart (last 30 15-min bars)                   │
│                                                                │
│ 🟢 WHY: 20D breakout + RS ≥ 80 + volume 2×20d avg              │  ← Danelfin
│                                                                │
│ Buy zone:  ₹2,450 → ₹2,573 (entry to pivot+5%)                 │  ← MarketSmith
│ SL ₹2,410  Target ₹2,530  R:R 2.3  Qty 12 (ATR-Kelly 0.5×)     │  ← evidence-based
│                                                                │    sizing labeled
│ Thesis: Nifty IT breadth expansion + earnings drift day 3      │
│ Invalidates if: Nifty breaks 24800 OR volume <1× 20d avg       │
│ [📱 Open in Kite]  [📝 Paper-trade this]  [🔕 Mute 24h]        │
└────────────────────────────────────────────────────────────────┘
```

**Card renders identically in the PWA push notification body**, minus the spark chart (text-only): `RELIANCE BUY @ 2450, SL 2410, tgt 2530, R:R 2.3, qty 12. WHY: 20D breakout + RS≥80 + vol 2×.`

### 5.4 Signal JSON schema (source of truth)

```json
{
  "signal_id": "swing-2026-07-15-RELIANCE-1035",
  "engine_version": "v2+wave-a@<git_sha>",
  "mode": "SWING",
  "action": "BUY",
  "symbol": "RELIANCE",
  "generated_at": "2026-07-15T10:35:00+05:30",
  "input_hash": "sha256:...",
  "confidence": 0.82,
  "tape_regime": "MIXED",
  "top_features": ["20D breakout", "RS ≥ 80", "volume 2× 20d avg"],
  "buy_zone_low": 2450.00,
  "buy_zone_high": 2573.00,
  "entry": 2450.00,
  "stop_loss": 2410.00,
  "target": 2530.00,
  "risk_reward": 2.3,
  "quantity": 12,
  "sizing_method": "atr_fractional_kelly_0.5",
  "position_pct": 3.5,
  "thesis": "Nifty IT breadth expansion + earnings drift day 3",
  "invalidation": "Nifty breaks 24800 OR volume <1× 20d avg",
  "spark_chart_url": "signals/2026-07-15/RELIANCE-spark.png",
  "kite_deeplink": "https://kite.zerodha.com/chart/web/ciq/NSE/RELIANCE/day"
}
```

### 5.5 Engine Performance Report (Wealthfront pattern)

Dedicated page at `pages/1_Engine_Performance.py`. Auto-refreshed weekly by walk-forward job. Contents:
- Expected vs realized expectancy per regime (TRENDING / MIXED / HOSTILE)
- Cumulative paper equity since inception with drawdown shading
- The **-1.61% HOSTILE held-out 2026 finding cited as first-class result**, never hidden
- Explicit exclusion criteria the engine applies (Value Research pattern): "excluded from universe: N stocks with <3-yr history, N with earnings gaps >15%, N in HOSTILE-tape sectors"
- Every closed paper trade linkable (Booming Bulls anti-pattern: never hide losses)

### 5.6 Mode switch behavior

- Radio at top of Today, state persisted in `localStorage` + query param.
- **Only the active mode fires alerts** — prevents notification spam when the user is in "swing mindset" during market hours.
- Both modes write to the audit log regardless — mode switch is a *notification filter*, not a data filter.
- Default mode logic: current IST time 09:15–15:30 → INTRADAY, else SWING. Overridable, sticky per user.

### 5.7 Trust badge (top of every page)

Three states, all "loud when broken":

| State | Meaning | UI treatment |
|---|---|---|
| 🟢 Healthy | Last pipeline run < 10 min ago, Fyers live, no errors in last 24h | Green, plus source indicator "Live prices ON (Fyers)" |
| 🟡 Degraded | Last run 10–30 min ago OR Fyers down (yfinance fallback active) OR ≥1 error in last 24h | Amber, "Live prices degraded — showing yfinance 15-min lag" |
| 🔴 Dead | Last run > 30 min ago | Red, signals section replaced by "pipeline down, do not trade off cached signals"; PWA push already fired separately |

## 6. Pipeline internals

### 6.1 Cron schedule

```yaml
# .github/workflows/signal-pipeline.yml (in nse-trading-lab, PUBLIC repo)
on:
  schedule:
    # NSE hours 09:15–15:30 IST = 03:45–10:00 UTC, Mon–Fri
    - cron: '*/5 3-10 * * 1-5'   # 5-min interval
  workflow_dispatch:               # manual trigger from Actions tab
```

Documented reality: GitHub Actions cron can delay 10–15 min under high load. This is *why* the trust badge and staleness gates exist. Public code repo → unlimited free Actions minutes. Private signals repo → we only push results there (~one commit per run), consumes near-zero of the 2000-min/mo private quota.

### 6.2 Per-tick execution flow

```
1. ACQUIRE
   ├─ Fyers /quotes for Nifty 50 (live LTP)
   ├─ yfinance 15-min bars (fallback + computation)
   ├─ yfinance daily Nifty (regime, 1h cache)
   ├─ NSE public JSON best-effort (cross-check)
   └─ input_hash = sha256(sorted concat of last bars + LTPs)
        → if input_hash == prev_hash: SKIP (no market change)

2. GATES
   ├─ staleness_gate: any bar >20 min old → STALE flag
   ├─ dual_source_gate: Fyers vs yfinance vs NSE — divergence >0.5%
   │   → DIVERGENT flag, health.json update, alert
   └─ market_hours_gate: outside 9:15–15:30 IST → SKIP compute

3. COMPUTE
   ├─ assess_tape(nifty_df) → regime + regime_conditions progress %
   ├─ SWING: analyze_swing() + top_features extraction + ATR-Kelly sizing
   ├─ INTRADAY: scan_rsi() + 5 safety gates + top_features
   └─ each produces Signal objects per §5.4 schema

4. DEDUPE
   ├─ Signal.input_hash checked vs signals/*.json today
   └─ identical signal already fired → don't re-alert (still log)

5. PERSIST (writes to PRIVATE signals repo via deploy key)
   ├─ append signals/YYYY-MM-DD/HH-MM.json (immutable)
   ├─ overwrite state/latest.json (UI reads)
   ├─ overwrite state/pipeline_health.json (heartbeat)
   └─ auto-fill paper_ledger.parquet for yesterday's signals that
      hit SL/target/time-stop today

6. NOTIFY (new non-deduped signals only)
   ├─ Web Push: POST to VAPID endpoint per subscribed device
   ├─ Fallback: 5xx → alert_queue.json, health.json.alert="degraded"
   └─ Cap: 3 alerts/mode/day (spam guard)

7. HEARTBEAT + SELF-CHECK
   ├─ health.json.last_run_ts = now()
   ├─ any step 2/3/5/6 raised → health.json.status = "degraded"
   └─ separate 15-min workflow: if last_run_ts >15 min → "pipeline
      dead" push
```

### 6.3 Error handling matrix (loud-when-broken taxonomy)

| Failure | Caught by | Evidence | Action |
|---|---|---|---|
| Fyers 4xx/5xx / token expired | try/except in fetch | health.json.errors[] + trust badge amber | fall back to yfinance, alert user to refresh token |
| yfinance 4xx/5xx | try/except in fetch | health.json.errors[] | retry once, then STALE flag |
| yfinance returns empty df | schema validator | health.json.errors[] | skip symbol, log, continue |
| Tape assessor crashes | try/except in step 3 | health.json.errors[] | skip regime update, use last cached, alert |
| GH Actions job dies | separate heartbeat workflow | pipeline_health.json stale | "pipeline dead" push |
| VAPID push endpoint 5xx | try/except in step 6 | alert_queue.json | queue, UI shows amber |
| Deploy key expired | Actions log | Failed step in Actions UI | GH emails user (built-in) |
| Paper-ledger auto-fill lags | step 5 self-check | trust ledger gap | warning banner on equity chart |
| Two conflicting signals same symbol | dedupe conflict rule | audit log CONFLICT event | emit event, no notification, alert in UI |

### 6.4 Additional scheduled workflows

- **End-of-day rollup** (15:35 IST, one run): close all open intraday paper trades, compute daily P&L, append to trust ledger, fire "today's digest" push (`3 signals · 2 paper hits · +0.4R engine tracking`).
- **Weekly walk-forward refresh** (Sunday 06:00 IST): regenerate `output/walk_forward/*.md` from last 6 months of trades, update baseline expectancy shown on equity chart. Baseline stays honest automatically.
- **Monthly universe refresh** (1st of month): re-pull Nifty 50 constituent list; universe changes push a "universe changed" notification.
- **Fyers token nag** (09:00 IST daily): if token expired, push notification to refresh before market open.

## 7. Trust ledger (schema + honesty guarantees)

### 7.1 Three parquet tables (all in private signals repo)

**`signals.parquet`** (append-only, one row per fire)
```
signal_id           TEXT PK    swing-2026-07-15-RELIANCE-1035
generated_at        TIMESTAMPTZ
engine_version      TEXT       git SHA of code repo
input_hash          TEXT       sha256 of inputs
mode                ENUM       SWING | INTRADAY
action              ENUM       BUY | SELL | EXIT
symbol              TEXT
top_features        ARRAY<TEXT>
entry, stop_loss, target, buy_zone_low, buy_zone_high  FLOAT
quantity            INT
sizing_method       TEXT
confidence          FLOAT
tape_regime         ENUM       TRENDING | MIXED | HOSTILE
thesis              TEXT
invalidation        TEXT
was_notified        BOOL       false if deduped or gated
gate_failures       ARRAY<TEXT>
```

**`paper_trades.parquet`** (outcome per signal, auto-filled by rollup)
```
signal_id           TEXT PK/FK → signals
entry_price         FLOAT      NEXT bar's open (conservative)
entry_ts            TIMESTAMPTZ
exit_price          FLOAT      NULL while open
exit_ts             TIMESTAMPTZ
exit_reason         ENUM       TARGET | STOP | TIMESTOP | INVALIDATED | REGIME_FLIP | OPEN
pnl_pct             FLOAT
pnl_r_multiple      FLOAT      pnl_pct / (entry - stop_loss)% — Van Tharp
hold_bars           INT
slippage_pct        FLOAT      modeled 0.1% conservative
```

**`equity_daily.parquet`** (one row per day per mode)
```
date                DATE PK
mode                ENUM SWING | INTRADAY
signals_generated   INT
signals_notified    INT
trades_opened       INT
trades_closed       INT
win_rate            FLOAT
avg_r_multiple      FLOAT      the expectancy metric (primary UI display)
avg_pnl_pct         FLOAT      secondary
cumulative_pnl_pct  FLOAT      running product
tape_regime_days    STRUCT     {trending: N, mixed: N, hostile: N}
```

### 7.2 Paper-trade auto-fill state machine

```
                       ┌──────────────────────┐
     signal fires ───▶ │  OPEN                │
                       │  entry = next bar    │
                       │  open (conservative) │
                       └──────────┬───────────┘
              ┌───────────────────┼─────────────────────┐
              ▼                   ▼                     ▼
      high >= target       low <= stop_loss     invalidation triggered
      → TARGET             → STOP               → INVALIDATED
                                  │
                       held > time_stop bars
                       → TIMESTOP
                                  │
                       tape flips HOSTILE mid-hold (SWING only)
                       → REGIME_FLIP
```

Entry price = next bar's open (honest — you couldn't have gotten in earlier).  
Slippage: 0.1% adverse on entry + exit.  
Costs modeled: STT (0.025% sell), brokerage (₹20 flat Zerodha), GST per trade.  
Knobs in `paper_ledger_config.yml` — one place, versioned, auditable.

### 7.3 Live-vs-backtest divergence math (the Composer-gap chart)

```python
# Recomputed nightly, published as state/divergence.json
window = last 30 closed paper trades in current mode
paper_expectancy_r  = mean(paper_trades.pnl_r_multiple in window)
baseline_expectancy_r = walk_forward.expectancy_by_regime_r[current_mix]
baseline_std_r        = walk_forward.std_by_regime_r[current_mix]

sigma_units = (paper_expectancy_r - baseline_expectancy_r) / (baseline_std_r / sqrt(30))

status = (
  "engine tracking"      if abs(sigma_units) < 1
  else "amber, watching" if abs(sigma_units) < 2
  else "engine drift, DO NOT TRADE"   # kill-switch
)
```

Chart: X = date, Y = cumulative_r_multiple. Two lines — paper (green) and backtest baseline (blue, dashed). Gap filled amber if divergent. `status` string bold above chart.

### 7.4 Kill-switch behavior

When `status == "engine drift, DO NOT TRADE"`:
1. UI hides `[📱 Open in Kite]` action from every signal card
2. Signal cards get red-bordered "kill-switch active" ribbon
3. PWA push still fires but body includes `⚠ KILL-SWITCH: paper divergence`
4. Only manual typed-phrase override in Settings can re-enable actions (mirrors existing HOSTILE override pattern)
5. Kill-switch clears automatically once rolling divergence returns to <1σ

**Alternative considered**: soft warning banner without hiding action. Rejected because the point of "blindly trustable" is that the system refuses to let you act when its own evidence says its edge has decayed. Paternalistic by design.

### 7.5 Honesty guarantees (enforced by code, not operator)

- **Signals table append-only** — no `UPDATE`, no `DELETE`. Git history in signals repo = tamper-evidence.
- **Paper trades not editable** — auto-fill runs once per signal, row frozen thereafter.
- **Baseline expectancy from `output/walk_forward/*.md`** — regenerated weekly against held-out data. Baseline updates automatically when engine changes. Cannot manually pin favorable baseline.
- **The -1.61% HOSTILE number stays visible** in the equity chart's baseline line permanently — full regime picture always shown.
- **Idempotent computation** — `python scripts/rebuild_ledger.py --from-scratch` produces bit-identical results.

### 7.6 What the ledger is NOT

- Not a database. Parquet + git. No SQLite race conditions, no Postgres to keep alive.
- Not backfilled. Only live-fired signals count. Backtests are already in `output/walk_forward/`.
- Not editable via UI. UI is read-only against the ledger.

## 8. Evidence-based strategy overlays (Loop 3.5)

### 8.0 Intraday strategy exploration loop (kills darlings, keeps only survivors)

Design principle confirmed with user 2026-07-15: **optimize expectancy (R-multiple), not win rate.** Realistic Nifty-50 intraday ceiling = 40–55% win rate, 0.1R–0.3R expectancy after honest 0.1% round-trip Zerodha costs. Anything higher is likely overfit or survivorship-biased marketing. Van Tharp (2007), Curtis Faith (2007) are canonical citations.

Loop 3.5 begins with a **5-hypothesis intraday backtest** on 6 months of held-out 2026 data with honest costs. Only hypotheses that show ≥+0.2R expectancy after costs ship into the pipeline. Others are buried in the audit log (never retried without new evidence).

| # | Hypothesis | Cite | Prior expectation |
|---|---|---|---|
| 1 | Opening Range Breakout (30-min ORB, Nifty 50) | Crabel (1990); Zarattini & Aziz (2023 SSRN, SPY 5-min ORB) | Weak-to-moderate positive, regime-dependent |
| 2 | PEAD earnings drift (2-day continuation after >2% earnings gap) | Bernard & Thomas (1989, *JAR*); Sehgal & Bijoy (2015, *Decision*, India) | Positive edge, low frequency — best a priori candidate |
| 3 | VWAP mean-reversion with volume filter | Berkowitz, Logue, Noser (1988); Madhavan (2002) — as execution benchmark | Likely no alpha, worth falsifying |
| 4 | Gap fade (opening gap >1σ reverts within 30 min) | Retail lore; Kaufman (2020, *Trading Systems and Methods* 6th ed) | Regime-dependent, needs volatility filter |
| 5 | Time-of-day mean reversion (14:00 IST reversal after morning trend) | Indian-market lore; no citable academic backing | Test to falsify, expect no edge |

**Existing v2 intraday scanner (15-min RSI)** — re-backtested with honest costs in this same batch. Prior work shows negative expectancy at every threshold; if this replicates, the RSI scanner is deprecated (kept in code, disabled in the pipeline). Anti-pattern: never keep a strategy just because it exists.

Every backtest reports: expectancy (R), win rate, max drawdown, Sharpe, sample size, and cost breakdown. Results ship to a public `output/loop_35_intraday_backtest_verdict.md` alongside the existing walk-forward outputs.

### 8.1 ATR fractional-Kelly position sizing (highest evidence)

- Formula: `position_size = (0.5 * kelly_fraction * capital) / (ATR_20 * kelly_multiplier)`
- Fractional Kelly (0.5×) instead of full Kelly per Thorp (2011) — full Kelly has 50% drawdowns.
- Cite: Kelly (1956, Bell System Tech J); Thorp (2011, *The Kelly Capital Growth Investment Criterion*, World Scientific); Van Tharp (2007, *Trade Your Way to Financial Freedom*, 2nd ed. McGraw-Hill); Curtis Faith (2007, *Way of the Turtle*, McGraw-Hill).
- Effort: 2–4 hrs (mostly refactoring existing Kelly module to be ATR-normalized).
- Expected impact: highest — turns +7.55% TRENDING expectancy into a survivable equity curve.

### 8.2 PEAD earnings-drift overlay

- Post-Earnings-Announcement Drift: continuation in the 1–3 days following an earnings surprise.
- Cite: Bernard & Thomas (1989, *Journal of Accounting Research* 27); Sehgal & Bijoy (2015, *Decision*, India replication on NSE).
- Implementation: NSE corporate-action feed → detect earnings day → if next-day open gaps >2% same direction as beat/miss → issue drift-continuation signal for 2 trading days.
- Effort: ~1 day.
- Expected impact: new signals distinct from tape-regime output, evidence-backed.

### 8.3 6–12 month cross-sectional momentum filter

- Only take longs in top-quartile 6M-momentum Nifty names during TRENDING regime.
- Cite: Jegadeesh & Titman (1993, *JoF* 48); Asness, Moskowitz & Pedersen (2013, *JoF* 68); Sehgal & Balakrishnan (2002); Sehgal & Jain (2011).
- Effort: ~1 day (compute 6M return per Nifty 50 stock, rank, filter).
- Expected impact: reduces false positives in MIXED regime — compounds regime gate.

**Explicitly skipped** (no evidence base):
- More RSI variants / new oscillators — zero incremental evidence
- Sentiment scraping — noisy, unreliable
- ML/AI black-box scores without explainability — violates trust principle
- Options mode — retail edge documented as thin

### 8.4 Referenced books (methodology + strategy)

Books cited or drawn on in this spec. Included so future implementation can trace claims back to sources.

**Methodology + honest backtesting:**
- Van Tharp (2007). *Trade Your Way to Financial Freedom* (2nd ed). McGraw-Hill. — R-multiple expectancy framework; the core reframe for §8.0
- Curtis Faith (2007). *Way of the Turtle*. McGraw-Hill. — low-win-rate trend following case study
- Marcos López de Prado (2018). *Advances in Financial Machine Learning*. Wiley. — Ch 11 on backtest overfitting is essential before shipping any §8.0 hypothesis
- Rob Carver (2015). *Systematic Trading*. Harriman House. — retail-appropriate position sizing + volatility targeting
- Andrew Lo (1999). *A Non-Random Walk Down Wall Street*. Princeton. — Sharpe reality-check (retail ceiling 0.6–1.0)

**Strategy encyclopedias:**
- Perry Kaufman (2020). *Trading Systems and Methods* (6th ed). Wiley. — reference for gap-fade, ORB, and dozens of setups
- Toby Crabel (1990). *Day Trading with Short Term Price Patterns and Opening Range Breakout*. Traders Press. — ORB origin

**Indian-market case studies:**
- Vijay Kedia (2022). *How I Made ₹2,000 Crores from the Stock Market*. — swing/positional, not intraday
- Sehgal & Balakrishnan (2002), Sehgal & Jain (2011), Sehgal & Bijoy (2015). — Indian equity replications of momentum + PEAD

**Explicitly warned against** (per research round 2, 2026-07-15):
- Anish Singh Thakur / Booming Bulls, GTF, TradingFuel, "millionaire in X days" titles. Research documented deleted negative comments, doctored MTM screenshots, hidden broker-statement losses.

## 9. Rollout plan (four shipping loops)

| Loop | Ships | User-visible value | Effort |
|---|---|---|---|
| **L1** (Week 1) | Private signals repo + deploy key + GH Actions workflow. Pipeline computes tape regime, writes `latest.json`, PWA manifest + service worker + VAPID push. One SWING signal type end-to-end. Regime cockpit + trust badge on Today page. **Cull: Dashboard, Learn, Settings → Advanced drawer.** | Open PWA on phone, see live regime + get first push notification. | ~1 week |
| **L2** (Week 2) | Signal card with full Univest schema + Danelfin explainability + MarketSmith buy-window band. Kite deep-links. WHY-NOT-TRADING surface for HOSTILE. Rebalance diff. Mode switch. INTRADAY lane wired. **Cull: Risk Lab folded into Today, Analyze + Trading Modes → Advanced.** | HOSTILE days show prep board. Push notifications carry full order params. | ~1 week |
| **L3** (Week 3) | Trust ledger schemas + append-only invariant. End-of-day rollup + auto-fill. Live-vs-backtest equity chart. Divergence math + `status` string. Reproducibility rebuild script. **Cull: Track Record → equity chart, Trade Replay → Advanced.** | App shows live proof it works or doesn't. Every signal has an outcome. | ~1 week |
| **L3.5** (Week 3.5) | **5-hypothesis intraday backtest** (§8.0) — kill darlings, keep survivors. Then: ATR fractional-Kelly sizing (§8.1), PEAD overlay if it survives §8.0 (§8.2), 6–12M momentum filter (§8.3). Engine Performance Report page. | Intraday strategies proven by honest backtest, sizing math applied. Whatever fails §8.0 stays deprecated. | ~1 week |
| **L4** (Week 4) | Kill-switch. Weekly walk-forward refresh. Monthly universe refresh. Self-heartbeat + pipeline-dead alerter. Daily digest push. Fyers token nag. **Cull: Decay Watch → Positions, Watchlist → Today prep board.** | Fully self-healing, fully honest. Ready to trade off real money if paper ledger proves edge. | ~1 week |

### 9.1 Page-cull final decision

| # | Page | Fate | Reason |
|---|---|---|---|
| 1 | Picks | **Replaced by Today** | Today ⊃ Picks + tape + WHY-NOT + mode switch |
| 2 | Dashboard | **Advanced drawer** | Regime cockpit replaces it |
| 3 | Analyze | **Advanced drawer** | Deep-dive niche, kept for research |
| 4 | Screener | **Keep** | Full-universe scan, distinct from Today top-3 |
| 5 | Trading Modes | **Advanced drawer** | Redundant with mode switch |
| 6 | Backtest | **Keep** | Research tool, distinct from live pipeline |
| 7 | Positions | **Keep** | Live MTF tracking, distinct purpose |
| 8 | Risk Lab | **Folded into Today** | Sizing shown per signal card |
| 9 | Journal | **Keep** | Real (not paper) trade log |
| 10 | Learn | **Advanced drawer** | Reference, low frequency |
| 11 | Settings | **Advanced drawer** | Config, low frequency |
| 12 | Decay Watch | **Folded into Positions** | Same underlying data |
| 13 | Tape Monitor | **Keep** | Regime deep-dive (drill from cockpit) |
| 14 | Track Record | **Folded into equity chart** | Replaced by live ledger |
| 15 | Trade Replay | **Advanced drawer** | Post-mortem, low frequency |
| 16 | Watchlist | **Folded into Today prep board** | Prep board = smarter watchlist |
| 17 | Intraday Scanner | **Folded into Today INTRADAY mode** | Same signal, better wrapper |

Result: 17 → 6 visible (Today, Screener, Backtest, Positions, Journal, Tape Monitor) + Advanced drawer. Nothing deleted; Streamlit multi-page nav just hides. Reversible.

### 9.2 Migration risks & mitigations

| Risk | Mitigation |
|---|---|
| Existing watchlist / journal lost | Both live in `~/.nse-trading-lab/` — untouched. Today prep board reads from watchlist JSON. |
| Paper trades double-counted with real journal | Journal keeps real trades only; paper ledger is new and separate. No overlap by design. |
| Advanced drawer breaks bookmarks | Streamlit auto-routes by filename — old URLs still work. |
| Push notification spam during regime flip | Cap 3 alerts/mode/day + digest aggregation. |
| GH Actions cron delayed >15 min | Heartbeat workflow fires "pipeline dead" push; UI red badge. |
| Fyers token expires mid-day | Fall back to yfinance; UI amber badge; nag push. |
| yfinance rate-limits | Retry once, STALE flag; visible in health.json. |

### 9.3 Success metrics (measured over 4 weeks post-Loop-4)

- Pipeline uptime > 99% during NSE hours (heartbeat log).
- Paper ledger ≥ 20 closed trades per mode (enough for divergence math).
- HOSTILE-day PWA opens > 0 (behavioral proof of WHY-NOT-TRADING surface).
- User takes zero real trades in Loops 1–3 (paper-only until ledger proves >1σ tracking).
- User takes a first real trade in Loop 4+ only if paper edge is proven.

### 9.4 Deferred to v2 (only if paper edge proves worthy)

- Kite Connect order integration (auto-order + live P&L) — ₹500/mo + 3 weeks + doubles blast radius
- 1-min bars via Kite/Fyers WebSocket (serious intraday) — needs always-on VPS
- SMS/Telegram fallback if PWA push proves flaky
- Nifty 100 / 200 universe expansion
- Options mode (thin evidence)

### 9.5 Rollback plan

- Streamlit pages: `git revert <SHA>` — back to previous state.
- Pipeline workflow: disable in GH Actions UI — cron stops, UI shows "pipeline dead."
- Signals repo: append-only — nothing to roll back; new signals just stop.

## 10. Anti-patterns explicitly codified

- **No confetti, no streak counters, no green-day-only pushes** — Robinhood anti-lesson. UI must reinforce HOSTILE = don't trade, not celebrate luck.
- **No hidden performance data** — every losing paper trade visible (opposite of Booming Bulls-class competitors).
- **No unverifiable "N% accuracy" headline** — replace with linked full trade log (opposite of Univest).
- **No auto-renewing anything** without one-tap disable (opposite of common India-app complaints).
- **No claim of edge without a citation or a walk-forward** — every strategy overlay in §8 documents its evidence source. No "trust me" signals.
- **No showing lagged prices as if they were live** — trust badge always names the source and freshness.

## 11. Open questions to resolve during implementation

- Fyers access-token refresh cadence — is it daily like Kite, or longer? Verify at implementation, adjust nag frequency accordingly.
- Web Push on iOS 16.4+ requires PWA installed to home screen — verify user's device supports it; if not, add explicit fallback plan (Telegram or email).
- Exact Kelly fraction to use — 0.5× is a conservative starting point but could be tuned via walk-forward on user's held-out data before Loop 3.5 ships.
- PEAD signal universe — Nifty 50 only, or Nifty 100 to capture more earnings events? Ties to future universe-expansion decision.

## 12. Success = "Kumar opens the app during a HOSTILE day and finds it valuable"

Everything above is in service of this behavioral test. If it fails, redesign has failed. If it passes, we've built something no Indian retail app currently ships.
