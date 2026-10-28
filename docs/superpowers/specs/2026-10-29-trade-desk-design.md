# Trade Desk — Design Spec

**Date:** 2026-10-29
**Owner:** Kumar Gaurav
**Status:** Approved direction (user chose all four value areas; asked to build and commit)
**Builds on:** `2026-07-15-trustable-intraday-swing-redesign.md` (Loop 1 branch `feat/l1-pipeline-pwa`)

## 0. Problem

The user: *"it does not give me valuable inputs at all — no use for me in current implementation."*

Root cause: the app has exactly one output — a ranked Nifty-50 swing pick list — and its measured edge is
regime-dependent (held-out 2026: −1.61% expectancy). In HOSTILE tape the regime gate correctly blocks
everything, so the only thing the user sees is "0 picks". A screen that only ever says *no* is useless,
even when it is right. The 17 pages split the remaining value across places nobody visits.

Goal: **one dashboard that gives a complete, honest next action every day, sized for minimum risk.**
"Minimum risk" is engineered (max loss per trade, daily limits, kill switches, blocks on missing data), not
promised. The desk must still be free to say "no trade" — but it always says *why* and *what would change it*.

## 1. What the desk gives (the four value areas, all chosen by the user)

| Tab | Question it answers | Output |
|---|---|---|
| **Today's Plan** | "What should I do today?" | Regime in plain words + what would flip it; 0–3 TAKE tickets; WATCH list with exact trigger prices; positions needing action |
| **Check a Trade** | "I like X — should I?" | Full plan (entry/stop/targets), qty sized to max loss, odds from the stock's own history, PASS/WARN/BLOCK per rule, one verdict |
| **My Positions** | "What do I do with what I hold?" | HOLD / TIGHTEN_STOP / EXIT per position, new stop, R so far |
| **Intraday** | "Any intraday setup worth taking?" | Opening-range breakout scan on 15-min bars; a setup is **only shown as tradeable** if its own replay on that stock is positive after costs |

Every verdict is one of **TAKE** (all hard checks pass), **WAIT** (a named trigger isn't met yet — shows the
trigger price), or **SKIP** (a hard check blocks — shows which). Nothing is ever placed: TAKE produces a
Kite-ready order ticket the user types in by hand.

## 2. Sources and what was taken from each

| Source | Verdict | What we use |
|---|---|---|
| `Jakeschincariol/day-trader-skill` (MIT) | Port ideas, NSE-native rewrite | Pipeline shape plan → odds → size → PASS/BLOCK → human yes; **Time Machine** (Monte Carlo from the stock's own bars + replay of every past time the setup fired); sizing "which limit decided it"; ticket with explicit yes. US parts (SEC, Pelosi, Alpaca, NYSE, FINRA) dropped. Attribution in module docstrings. |
| Freqtrade (GPL-3, crypto-only) | Ideas only | *Protections* → desk checks: stop-loss streak guard, daily max-loss lock, cooldown after a loss, low-expectancy symbol lock. Dry-run (paper) first. |
| NSE official archives | Adopt (direct HTTP, cached) | F&O ban list CSV. Unavailable → WARN "could not verify", never silent PASS. |
| Zerodha hosted Kite MCP (`mcp.kite.trade`) | Adopt for Claude sessions only | Read-only portfolio context in Claude Code via project `.mcp.json`; no order tools exist on the hosted server. The app does not depend on it. |
| `nsepython`, `NseIndiaApi` (GPL-3) | Not imported | Repo is public MIT; we fetch the NSE files ourselves. |
| TradingAgents, FinRobot | Skip | LLM in the signal path, non-reproducible. Breaks the deterministic-engine rule. |
| OpenAlgo (AGPL) | Idea only | Paper ledger + approval queue pattern (already in our ledger direction). |
| Jesse (crypto-only) | Idea only | Monte Carlo trade-shuffle + candle-noise robustness → Time Machine §3.3 step 3 |
| day-trader-skill `trader-news`, `trader-analyst` | Vendor as Claude skills for US, only if the security audit passes | SEC 8-K + headlines, XBRL earnings, used in Claude sessions — never inside a verdict |
| ECC (`affaan-m/ECC`, MIT) | Vendored review agents | `python-reviewer`, `security-reviewer`, `silent-failure-hunter` in `.claude/agents/` (pin b92f181b) review every slice. |

## 2a. Two markets (user trades both NSE and US)

The desk core is market-agnostic. A frozen `MarketProfile` carries everything market-specific, and every
function that needs one takes it explicitly (no globals):

| Field | NSE | US |
|---|---|---|
| `code` | `NSE` | `US` |
| `currency` | `₹` | `$` |
| `tz` | `Asia/Kolkata` | `America/New_York` |
| session open / close | 09:15 / 15:30 | 09:30 / 16:00 |
| intraday no-new-entry before / after | 09:30 / 14:30 | 09:45 / 15:00 |
| intraday flat-by | 15:15 | 15:55 |
| regime index (yfinance) | `^NSEI` | `^GSPC` |
| yfinance suffix | `.NS` | none |
| round-trip cost, swing | Zerodha CNC model (`picker_replay._delivery_costs_pct`) | SEC fee 0.00278% of sell + FINRA TAF $0.000166/share (max $8.30) + spread |
| round-trip cost, intraday | Zerodha MIS: brokerage min(₹20, 0.03%)/side + 0.025% STT sell + fees + spread | SEC fee 0.00278% of sell + FINRA TAF $0.000166/share (max $8.30) + spread |
| tradeable-list check | F&O ban list | none |
| min liquidity (20d median turnover) | ₹5 Cr | $20 M |
| ticket format | Kite: `CNC/MIS LIMIT` + `GTT OCO` | broker-neutral bracket: `BUY qty SYM LIMIT x, stop-loss y, take-profit z, DAY/GTC` |

Capital and limits are stored per market (`limits_nse`, `limits_us`); amounts never mix currencies.
US PDT guard: optional setting `pdt_guard` (off by default; brokers may still apply the old rule) — when
on, ≥3 day trades in the last 5 sessions BLOCKs a new intraday trade.
Holidays: v1 uses weekday calendars; the stale-data check tolerates up to 3 calendar days for swing so
a holiday never falsely BLOCKs (documented limitation).

## 3. Architecture

New package `nse_backtest/desk/`, pure functions over DataFrames, no Streamlit and no network inside the
core (data is passed in). One file per concern:

```
desk/
  markets.py      MarketProfile + NSE, US profiles; universe lists (Nifty 50 / US large-cap 50)
  costs.py        round_trip_cost_pct(profile, mode, entry, exit, qty, df) -> float
  models.py       TradePlan, Sizing, Check, Odds, DeskVerdict dataclasses; Status enum PASS/WARN/BLOCK
  plan.py         build_plan(df, symbol, mode, entry=None, stop=None, target=None) -> TradePlan
  sizing.py       size_position(plan, limits) -> Sizing   (binding limit named)
  timemachine.py  odds(df, plan, replays=10_000, seed=19) -> Odds  (Monte Carlo + setup history)
  checks.py       run_checks(plan, sizing, odds, context) -> list[Check]
  verdict.py      decide(plan, sizing, odds, checks) -> DeskVerdict  (+ Kite ticket text)
  daily.py        daily_plan(universe_frames, nifty_df, context) -> DailyPlan
  positions.py    review_positions(positions, frames) -> list[PositionAction]
  intraday.py     orb_scan(intraday_frames, ...) -> list[IntradaySetup]
  nse_lists.py    fetch_fno_ban() with disk cache; returns (symbols, status)
  audit.py        append verdict to ~/.nse-trading-lab/desk_audit.jsonl with input hash
  cli.py          python -m nse_backtest.desk {check,plan,positions,intraday}
```

UI: `pages/0_Desk.py` — the new landing page with the four tabs. Existing pages untouched (Loop 1's
Today page stays). Claude Code: project skills in `.claude/skills/nse-desk-*` wrapping the CLI.

### 3.1 Plan (`plan.py`)
- Swing: entry = last close (or user's). Stop = max(entry − 2×ATR14, lowest low of last 10 bars × 0.999)
  — the higher of the two, i.e. less risk — then clamped so entry − stop ≥ 1×ATR14 (closer is noise).
  R = entry − stop. T1 = entry + 1.5R, T2 = entry + 3R, max hold 15 bars.
- Intraday: entry = opening-range high (first 15 min) breakout; stop = OR midpoint; T1 = 1R, T2 = 2R; flat by 15:15 IST.
- User-supplied entry/stop/target override defaults; a stop at or above entry is rejected (ValueError).

### 3.2 Sizing (`sizing.py`)
Limits (from settings, defaults shown): capital, `risk_per_trade_pct`=1.0, `max_position_pct`=25,
`cash_buffer_pct`=10. qty = floor(min(risk_budget / R, position_cap / entry, (capital × (1 − buffer)) / entry)).
Reports which limit bound. qty < 1 → BLOCK "stop too wide for your max loss" (never a silent smaller trade).
Costs: Zerodha CNC/MIS round-trip estimate included in max-loss.

### 3.3 Time Machine (`timemachine.py`)
1. **Monte Carlo**: block-bootstrap (block=5) of the stock's own last 250 daily log-returns (swing) or
   last 40 sessions of 15-min returns (intraday), 10,000 paths, fixed seed. Each path is simulated
   bar-by-bar using bootstrapped (open, high, low, close) ratios relative to the previous close, so
   intrabar stop/target touches are modelled; if both are touched in one bar, **stop wins** (conservative).
   Output: P(target first), P(stop first), P(time exit), expectancy in R after costs.
2. **Setup history**: replay the plan's own geometry (enter at close, same stop rule, T1 target, same
   max hold, stop wins on a both-touched bar, costs included) on every past bar of the last 3 years
   (swing) / 60 days (intraday ORB days) where the same entry condition held. Swing entry condition:
   close > EMA50 > EMA200 (trend setup). Non-overlapping: after a trade, skip to its exit bar.
   Output: n, win %, expectancy R. n < 20 → reported as "too few samples" (WARN).
3. **Robustness (Jesse-style)**, on the setup-history trades:
   - *Trade shuffle*: 2,000 random reorderings of the historical R sequence → 5th/50th/95th percentile of
     max drawdown in R, and P(5 losses in a row). Tells the user how bad a normal losing streak gets.
   - *Candle noise*: re-run setup history 20 times with each bar's OHLC jittered by ±0.25×(High−Low)
     (seeded, bar-consistent); report the expectancy range. If expectancy turns negative in > 25% of
     jittered runs → WARN "edge is fragile".
The odds are a reality check, not a forecast; the UI says so.

### 3.4 Checks (`checks.py`) — each returns PASS / WARN / BLOCK with a one-line reason
BLOCK: regime HOSTILE for swing longs (US uses the same classifier on ^GSPC); R:R to T1 < 1.5; qty < 1; risk governor says cannot trade
(max positions, cooling off, weekly kill switch, sector cap, aggregate risk); daily realised loss ≥
`daily_loss_limit_pct` (default 2%); symbol in F&O ban list; Monte Carlo expectancy < 0R; stale data
(last bar older than 1 trading day swing / 30 min intraday); intraday after 14:30 IST or before 09:30;
liquidity below ₹5 Cr 20-day median turnover.
WARN: regime MIXED; earnings inside the hold window; ban list unverifiable; setup history n < 20;
3 consecutive stop-outs in the journal (Freqtrade StoplossGuard) → also halves size; gap > 2% today.

### 3.5 Verdict (`verdict.py`)
Any BLOCK → SKIP. No BLOCK but a pending trigger (entry above current price) → WAIT with trigger price.
Otherwise TAKE. Ticket text: `BUY <qty> <SYMBOL> CNC LIMIT <entry>` + `GTT OCO: SL <stop> / TGT <T1>`
(MIS + SL-M for intraday). The verdict carries a SHA-256 of its inputs and is appended to the audit log.

### 3.6 Daily plan, positions, intraday
- Daily: per market, assess tape on the profile's regime index → run plan+checks for every universe stock with fresh data → TAKE (best 3 by MC
  expectancy) / WATCH (WAIT verdicts sorted by distance to trigger, top 5) / regime-flip conditions from
  `TapeAssessment` (e.g. "Nifty needs to close above 50-EMA at X").
- Positions: wraps `position_monitor.daily_check` + `exits.update_trail_stop`, adds R-so-far.
- Intraday: ORB on 15-min bars; each candidate gets the Time Machine *setup history* on its own last 60
  days; shown as tradeable only if history n ≥ 20 and expectancy ≥ +0.2R after costs (the project's
  shipping rule), else "research only" in grey.

## 4. Error handling
Core functions raise on invalid input (bad symbol, stop ≥ entry, empty frame). Network lives only in
`nse_lists.py` and the page/CLI loaders; any fetch failure becomes a WARN or BLOCK check, never a silent
PASS. A symbol whose data can't be fetched appears in the plan as "data unavailable", not omitted.

## 5. Testing
TDD, offline: synthetic frames from `sample_data` plus hand-built frames for edge cases; Monte Carlo
deterministic under the seed; NSE fetch mocked. Streamlit AppTest smoke test for `pages/0_Desk.py`.
Fix the stale `2_Dashboard.py` smoke test. Gate: `python3.13 -m pytest` green.

## 6. Non-goals
Order placement of any kind; LLM text in verdicts; options; daily-scan universe beyond Nifty 50 / US
large-cap 50 (Check a Trade accepts any symbol); 1-minute bars; exchange holiday calendars; FX conversion.
