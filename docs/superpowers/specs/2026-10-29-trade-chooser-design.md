# Trade Chooser — Design

Date: 2026-10-29 · Status: approved by the owner in conversation · Branch: `feat/desk-followups`

## 0. Why

The owner wants the desk to let them choose any trade and, for each one, see plainly how much they can earn,
how much they can lose, where the stop-loss and the targets are, and charts with honest projections. The desk's
verdict (TAKE / WAIT / SKIP) stays as advice backed by deterministic checks; the owner makes the final call and
types the ticket in themselves. The desk never places orders.

Owner decisions (2026-10-29):
- Freedom: every trade gets full numbers and charts; WAIT/SKIP tickets appear after an explicit
  "I choose this anyway", with the blocking reasons shown in red; overrides are logged and scored separately.
- Projections: trade outcome odds, price chart with levels and a range band, account projection, projected vs real.
- Skills: `trader-news` and `trader-analyst` feed a research panel that is information only, never a vote.
- Follow-ups folded in: intraday ticket becomes a stop-limit; Intraday-tab scan TAKEs are logged.

## 1. Architecture

New tab **"Choose a Trade"** in `pages/0_Desk.py`, first in the tab order. It shows one table of every
candidate from the daily plan and the intraday scan (TAKE, WAIT and SKIP, each with entry, stop, T1, T2, qty,
max loss, profit at T1, odds). Selecting a row opens a **trade card**. "Check a Trade" renders the same card
for a typed symbol.

New pure modules (no Streamlit, no network, unit-tested):

| Module | Responsibility |
|---|---|
| `nse_backtest/desk/tradecard.py` | `TradeCard` dataclass and `build_card(verdict, profile, limits, *, qty=None)`: money figures, limit breaches, ticket gating |
| `nse_backtest/desk/projections.py` | outcome distribution in money, price range band, account projection, projected-vs-real series |
| `nse_backtest/desk/charts.py` | Plotly figure builders from the above (pure: data in, `go.Figure` out) |
| `nse_backtest/desk/research.py` | runs the vendored skills as subprocesses and returns typed, display-only results |

Existing modules changed: `plan.py`, `verdict.py`, `timemachine.py`, `checks.py` (stop-limit entry),
`intraday.py` (log scan TAKEs), `audit.py` (override rows), `scoreboard.py`/`scoreboard_io.py` (stop-limit fill,
two records), `cli.py` (card text, `--choose`), `pages/0_Desk.py`.

## 2. Trade card (money, in the market's currency via `profile.money()`)

For a verdict `v` with plan `p`, sizing `s` (or a user qty override):
- `risk_per_share = p.entry − p.stop`; `cost = round_trip_cost(profile, mode, entry, target_1, qty)`.
- `max_loss = qty × (entry − stop) + cost` (loss if the stop fills at the stop price; gaps can lose more — say so).
- `profit_t1 = qty × (target_1 − entry) − cost`; `profit_t2 = qty × (target_2 − entry) − cost`.
- `rr_t1 = (target_1 − entry) / risk_per_share`; `expected_value = mc_expectancy_r × qty × risk_per_share`
  (None when odds are missing — never 0).
- `notional`, `% of capital at risk`, `% of capital deployed`.
- **Limit breaches**: a user qty above the sized qty is allowed; the card lists each limit it breaks
  (risk per trade, max position %, daily loss lock) in red. Qty ≤ 0 or non-integer → validation error.
- **Edits**: entry/stop/target edits re-run `check_trade` (same path as Check a Trade today), so checks, odds and
  money all recompute. Invalid geometry (stop ≥ entry, target ≤ entry) → card shows the error, no ticket.
- **Ticket gating**: TAKE → ticket shown. WAIT/SKIP → blocks/warns listed in red; ticket only after the
  "I choose this anyway" checkbox; the ticket header then reads "YOUR OVERRIDE — the desk said {action}".

## 3. Stop-limit intraday entry (ORB)

The Time Machine already models the intraday entry as a buy-stop at the opening-range high
(`timemachine._intraday_history`: fill = max(Open, OR high)). The ticket printed `MIS LIMIT`; make them agree.
- `plan.entry_limit(plan, profile) = round_up_to_tick(entry + min(0.25 × risk_per_share, 0.005 × entry))`
  (tick NSE 0.05, US 0.01). `TradePlan` gains `entry_limit: float | None` (intraday only).
- Tickets: NSE `BUY {qty} {sym} MIS SL trigger {entry} limit {entry_limit}` (then the existing SL-M stop and
  target lines); US intraday `BUY {qty} {sym} STOP-LIMIT stop {entry} limit {entry_limit} — bracket …, DAY`.
  Swing tickets unchanged (swing entries are limit orders and are replayed as such).
- One shared fill rule `stop_limit_fill(op, hi, lo, k_start, trigger, limit) -> (index, price) | None`, used by
  the Time Machine intraday history AND the scoreboard intraday replay:
  trigger bar k = first bar ≥ k_start with High ≥ trigger. If Open[k] ≤ limit → fill at max(Open[k], trigger).
  Else the order rests as a buy limit at `limit`: fill at `limit` on the first bar j ≥ k with Low ≤ limit
  (price = limit, never better). Never triggered or never back to the limit before flat-by → not filled.
  On the fill bar only the stop can trigger, exit at min(Open, stop) (existing invariant: LOST never > 0R).
- Live proximity check (`checks.py`, intraday): TAKE requires the live price ≤ entry_limit; above it →
  WAIT "price is above the stop-limit's limit ({limit}) — the order would not fill".
- Scoreboard: intraday rows use the logged `entry_limit`; old rows without it compute it with the same formula.
  `SCOREBOARD_VERSION` → 3.

## 4. Logging and two records

- `intraday.orb_scan` appends every TAKE verdict to the audit log (WAIT/SKIP from the scan are not logged).
- Override: when the owner ticks "I choose this anyway" and the ticket is shown, append an audit row with the
  verdict's fields plus `chosen_by: "owner"`, `desk_action: "WAIT"|"SKIP"`, `action: "OVERRIDE"`. The CLI gets
  `check … --choose` which prints the override ticket and logs the same row.
- Scoreboard candidates: `action == "TAKE"` → record **"Desk picks"**; `action == "OVERRIDE"` → record
  **"Your overrides"**. Stats, verdicts and charts are computed separately per record; the two are never
  pooled. Dedupe stays within a record.

## 5. Projections (seed 19, `np.random.default_rng`; every chart captioned
"range of past behaviour, not a forecast")

1. **Outcome odds**: `timemachine.monte_carlo` also returns the per-replay R array (new field
   `Odds.mc_rs: tuple[float, ...]`, capped at 10,000). `projections.outcome_money(card, odds)` → money P&L per
   replay = R × qty × risk_per_share (costs already in R); returns p5/p25/median/p75/p95, P(target/stop/time).
   Chart: histogram of money P&L with p5/median/p95 lines and stop/target markers.
2. **Price chart with range band**: last 120 daily bars (swing) or the current session's 15-minute bars
   (intraday) as candles; horizontal lines entry, stop, T1, T2 (and entry_limit for intraday). Band: block
   bootstrap of the stock's own close-to-close log returns (block 5, 2,000 paths) over `max_hold_bars` from the
   last close → 5–95% and 25–75% envelopes drawn to the right of the last bar.
3. **Account projection**: `projections.account(capital, risk_amount_per_trade, rs, n_trades, paths=2000)` —
   each path draws n_trades R values with replacement from the trade's replay R array (history R when MC is
   missing); P&L per trade = R × risk amount (fixed money risk, no compounding); returns capital percentile
   bands per trade index, P(max drawdown ≥ 10% of capital), P(finish below start). N selectable (10/20/50,
   default 20). Missing odds → "unavailable", never a fabricated curve.
4. **Projected vs real** (Scoreboard tab, per record): cumulative projected R (sum of mc_expectancy_r of
   resolved trades, in order) vs cumulative realised R; scatter of projected vs realised per trade. Trades
   without a prediction are excluded from the projected line and counted in the caption.

## 6. Research panel (display only)

- `research.us_news(symbol)` → runs `.claude/skills/trader-news/news.py --tickers SYM --json --dry-run
  --hours 168` with `python -I`, cwd = a temp dir, timeout 10 s; `research.us_facts(symbol)` → `trader-analyst/
  facts.py SYM --json`. Results cached per (symbol, kind) for 30 min in memory (page) — no disk writes.
- SEC User-Agent: from `SEC_USER_AGENT` env var only (no default containing anyone's details). Missing → the
  SEC parts show "SEC data unavailable: set SEC_USER_AGENT".
- NSE: next earnings date (`loaders.next_earnings`) and recent dividends/splits from yfinance actions.
- Every result carries `source`, `fetched_at`, items with links and dates; any failure → `Unavailable(reason)`
  shown on the panel. Symbols validated before use.
- Isolation: a test asserts that no module among checks, verdict, service, plan, sizing, timemachine, daily,
  intraday, scoreboard imports `research` (AST scan), and that `research` imports nothing from them except
  `markets`.

## 7. Error handling

Each card section (numbers, odds chart, price chart, account projection, research) renders independently; an
exception in one shows `st.error` with the reason in that section only. Missing odds → sections say
"unavailable" and give the reason; no zero-filled numbers or empty-looking charts.

## 8. Testing

- Pure modules: unit tests with fictional numbers (money math incl. costs, breaches, gating; stop-limit fill
  rule incl. gap-above-limit and rest-then-fill; outcome percentiles; band shape and monotone envelopes; account
  projection determinism and drawdown probability on a known R set; projected-vs-real series).
- Property test: across seeded random bars, every fill from the shared stop-limit rule lies in
  [trigger, limit], and the fill bar is never before the trigger bar.
- Charts: assert traces/shapes/annotations exist with the right values (no image snapshots).
- Research: subprocess runner tested with a fake script (timeout, bad JSON, non-zero exit) — no network.
- Page: AppTest smoke for the new tab (table renders, a row opens a card, override checkbox reveals the ticket).
- Gate: `python3.13 -m pytest -o addopts=""` green on the local stack and on a venv built from requirements.txt.

## 9. Out of scope

Placing orders; any LLM in the app; news or sentiment as an input to the verdict; NSE 2027 holidays (not
published yet).
