# Desk Scoreboard, Holiday Calendars, Faster Plan, Pipeline Tests — Design + Plan

**Date:** 2026-10-29 · **Owner:** Kumar Gaurav · **Status:** approved by the user ("go ahead and complete everything possible")
**Builds on:** `2026-10-29-trade-desk-design.md` (merged, main b5946cb)

## 0. Why
The desk records every verdict but never checks whether its TAKEs made money. Before real money, the user needs
the desk's own paper record compared with the odds it showed. Plus three gaps left open at merge: no exchange
holidays (false BLOCKs after long weekends, wrong behaviour on holidays/early closes), a slow sequential 50-symbol
plan, and pipeline tests that cannot import without the Fyers SDK.

## 1. Scoreboard (paper record of the desk's TAKEs)

**Source:** `desk_audit.jsonl` (already written for every verdict shown with audit=True: Check a Trade + daily TAKEs).
The audit row gains fields (append-only, old rows still readable): `max_hold_bars`, `cost_per_share`,
`history_expectancy_r`, `target_2`, `last_bar`, `current_price`, `stop_basis`. Missing in old rows → defaults
(swing max_hold 15; cost_per_share estimated with `round_trip_cost(profile, mode, entry, target_1, max(qty,1))/max(qty,1)`).

**Candidates:** rows with `action == "TAKE"`; dedupe by (market, symbol, mode, market-tz trading date of
created_at) keeping the first. A scoreboard is the desk's record as if every TAKE were taken exactly per its ticket —
it is not the user's real P&L.

**Fill and exit rules (conservative, same priorities as the Time Machine `_walk`):**
- *Swing* (CNC LIMIT at entry, valid one session; GTT OCO stop/T1): fill session = created_at's session if created
  before that session's close, else the next trading session. Daily bar of the fill session: filled iff
  `Low <= entry`; fill price = `min(Open, entry)`. On the fill bar only the stop can trigger
  (`Low <= stop` → exit at `min(Open, stop)` if Open <= stop else stop); target on the fill bar is ignored
  (intrabar order unknown → conservative). Then `_walk` the next bars up to `max_hold_bars` bars after the fill bar;
  no exit → time exit at the Close of the last bar. Not filled → `not_filled`.
- *Intraday* (MIS LIMIT at entry): 15-min bars of created_at's session, strictly after created_at. Filled at the
  first bar with `High >= entry`, fill = `max(Open, entry)`; on the fill bar stop-only check; then `_walk` the
  remaining bars before the session's flat-by; no exit → time exit at the Close of the last bar before flat-by.
  Not filled before flat-by → `not_filled`.
- *Status*: `won` (exit at target), `lost` (exit at/through stop), `time` (time exit), `not_filled`, `open` (not enough
  bars yet; mark-to-market R shown, excluded from stats), `data_unavailable` (bars can't be loaded, e.g. intraday
  older than 60 days and not cached) — never silently dropped.
- *Realised R* = (exit − fill − cost_per_share) / (entry − stop) using the planned R.

**Stats per (market, mode)** over resolved trades (won/lost/time): n, win %, mean realised R, mean predicted MC R,
mean predicted history R, mean(realised − predicted MC), 95% bootstrap CI of mean realised R (2,000 resamples,
seed 19), max drawdown in R, cumulative R series, counts of not_filled/open/unavailable.

**Verdict** (first that applies):
1. n < 30 → "Not enough trades yet ({n}/30) — keep paper-trading".
2. CI upper < 0 → "Losing — do not trade this with real money".
3. CI upper < mean predicted MC R → "Worse than the desk's odds — do not size up".
4. CI lower > 0 and mean realised ≥ mean predicted MC − 0.15 → "Matching the odds — real money at small size (≤0.5% risk)".
5. else → "Inconclusive — keep paper-trading".

**Persistence:** resolved final statuses (won/lost/time/not_filled) cached in `desk_scoreboard.json` keyed by the
audit row's input_hash + created_at, so resolved trades are not refetched; open/unavailable are recomputed.

**Surfaces:** CLI `scoreboard [--market NSE|US] [--json]`; page tab "Scoreboard" (verdict banner first, stats table,
cumulative R chart, per-trade table with status, fill, exit, realised vs predicted R).

## 2. Exchange calendars
`desk/calendars.py`: per-market holiday dicts for covered years (NSE 2026; NYSE 2026–2027 holidays and 1:00 pm early
closes) from official sources recorded in the module docstring; `is_trading_day(profile, d)`,
`session_close_for(profile, d)`, `previous_trading_day`, `trading_days_between(profile, a, b)`, `covered(profile, d)`,
`holiday_name(profile, d)`. Outside covered years: weekdays only, and callers add a WARN "holiday calendar not
available for {year}".
Wiring: swing freshness BLOCKs when more than 1 trading session has passed since the last bar (uncovered years keep
the 3-calendar-day rule + WARN); regime staleness uses the same trading-session rule (allow 2); intraday entry_window
BLOCKs on a holiday ("Market closed today: {name}") and on early-close days ("Early close today — no new intraday
entries"); `load_daily` partial-bar logic and the live-quote "market not open today" logic use `is_trading_day` +
`session_close_for`; the scoreboard uses the same calendar for fill sessions.

## 3. Faster daily plan
`loaders.load_daily_many(profile, symbols)`: symbols with a fresh `fetch_nse` disk cache are read from it; the rest are
fetched in ONE `yfinance.download(tickers, group_by="ticker", period/start-end, progress=False, auto_adjust=False,
threads=False)` call under `data._YF_LOCK`, split per ticker, written to the same cache, then each frame goes through
the same cleaning + partial-bar logic as `load_daily` (refactor into a shared `_finalise_daily`). Per-symbol failures
are returned as `{symbol: error}` and shown as unavailable. `session.load_frames` uses it.

## 4. Pipeline tests
`pipeline/fetch.py` imports `fyers_apiv3` lazily inside `_fyers_client()` (clear error if missing at runtime). The
gate becomes the full suite: `python3.13 -m pytest -q -o addopts=""` (no ignore). Fix any failing pipeline tests.

## 5. Plan (tasks; TDD, each reviewed; commit per task)
- **S1** Audit enrichment + `desk/scoreboard.py` pure core (candidates, dedupe, resolve swing/intraday, stats, verdict).
- **S2** `desk/calendars.py` + wiring (checks freshness/entry_window, regime staleness, loaders, live quote).
- **S3** Scoreboard I/O (`session.run_scoreboard`, cache file), CLI `scoreboard`, page tab, smoke tests.
- **S4** `load_daily_many` batch loading + session wiring.
- **S5** Lazy Fyers import; full-suite gate green.
- **S6** README/USAGE updates; ECC whole-branch review; fixes; merge to main (push only after asking).

Global constraints from the trade-desk plan still bind (deterministic, no orders, no LLM, fail closed, no network in
tests — tests/desk sockets are blocked, fictional numbers only, Co-Authored-By trailer, never `git stash`).
