# Hostile-Market Desk — Design

Date: 2026-10-30 · Status: approved by the owner (decisions below) · Branch: `feat/desk-hostile`

## 0. Why

On a HOSTILE Nifty day the desk said "TAKE: none, WATCH: none, Skipped 50" and the Choose a Trade table showed
"—" for every number. The owner: "what's the use of this app, it does not allow me to take trade at all".
Inputs: ECC product audit (`audit_ux.md`) and strategy research (`research_bear.md`, cited, skeptical).
Research summary: in a falling market the strongest evidence is for staying in cash; the only trade worth building
is an intraday opening-range breakdown short on liquid F&O stocks, and it must prove itself on the desk's own
history before the desk calls it TAKE. SEBI FY23 study: 71% of individual intraday traders lost money.

Owner decisions (2026-10-30):
- Regime stays a BLOCK; overrides stay allowed with a strong warning that puts the regime's held-out cost in money.
- When a trade's DATA is broken, "I choose this anyway" shows Re-check instead of a ticket.
- F&O ban: WARN for cash swing longs; BLOCK for intraday shorts.
- Build all six items below in one branch.

## 1. Stale-data top-up (done: fe0eb38)
A start/end daily download that lags the last completed session is topped up with one `period="5d"` request.
Rows with NaN prices (Yahoo's unfinalised day) are still dropped, so freshness still BLOCKs until Yahoo publishes.

## 2. "Your call" ranking (never throw computed numbers away)
- `daily.daily_plan` keeps every computed verdict: `DailyPlan.others: tuple[DeskVerdict, ...] = ()` (all verdicts
  that are neither in `take` nor `watch`). Symbols "not near a setup" are also checked, priced at the plan's own
  breakout entry (20-day high × 1.001, the existing swing default), so they come back as an honest WAIT/SKIP with
  numbers. `others` is never audited (only TAKEs are logged, as today).
- Split `daily_plan` into small helpers (pass 1, pass 2, overflow); catch any exception per symbol into
  `skipped` with its reason, so one symbol can't stop the plan.
- Normal-day bug: a 4th/5th TAKE beyond `top_take` goes to `others` as TAKE (not "SKIP — not checked").
- `session.merge_candidates(..., include_others=True)` ranks: TAKE → WAIT → advice-only SKIP (blocked only by
  regime/odds) → data/limit SKIP; within a group by MC expectancy descending (None last). Columns add "Exp R",
  "Expected value" (money) and "To entry %".
- Cards opened from a first-pass verdict (no live quote) show "Quick check — run a full check before choosing"
  with a Full check button (reuses Re-check); ticket and override only after the full check, whose TAKE is logged.
- "Check it" and Check a Trade use the plan's entry for the symbol (not the last close); the "0 = auto" field says
  what auto means.
- CLI: `plan --all` prints the ranked table with `check SYM --choose` hints.

## 3. Advice vs per-trade blocks
- Pure `verdict.block_groups(v) -> dict[str, list[Check]]` with groups: "market" (regime), "odds" (monte_carlo,
  setup_history, robustness), "data" (freshness, live_price, gap, price-scale, geometry such as price ≤ stop),
  "limits" (size, risk_governor, daily_loss, pdt, ban_list, stoploss_streak, liquidity, entry_window, reward_risk).
  Not a new field on Check (keeps input hashes stable).
- Card: market group as an amber "Market says sit out" banner with the flip conditions; odds as amber; data and
  limits red.
- Override header for a regime-blocked verdict: "YOUR OVERRIDE — against the market regime ({regime}): the desk's
  held-out longs here averaged {pct}% per trade ≈ {money(pct × notional)} on this position". The held-out figure
  comes from the regime assessment already shown in the tape banner; missing → the sentence without the number.
- Data-broken verdict (any BLOCK in the "data" group): no override checkbox; show Re-check. CLI `--choose` refuses
  with the reason and a re-check hint.
- `ban_list`: WARN for swing (CNC) longs ("in the F&O ban — derivatives only; cash delivery allowed"); BLOCK for
  intraday shorts (§5).

## 4. Intraday tab and scan
- Scan the whole universe (not the first 15) behind the existing button; show progress.
- Every scan row (TAKE/WAIT/SKIP) can open the same trade card (override per §3).

## 5. Opening-range breakdown short (ORB short), HOSTILE days only
- Mode `Mode.INTRADAY` with `side: Side = LONG|SHORT` on TradePlan (default LONG; hashes of existing longs unchanged
  — include side in the hash only when SHORT).
- Plan: OR = first 15-minute bar; entry trigger = OR low; stop = OR mid; target = entry − rr_t1 × R;
  `entry_limit` (sell limit) = round_down_to_tick(entry − min(0.25R, 0.5% of entry)).
- Eligible: market NSE, regime HOSTILE, symbol in the NSE F&O list (from the existing nse_lists module; unavailable →
  BLOCK "could not verify F&O eligibility"), not in the F&O ban (BLOCK), liquidity as longs, relative volume of the
  OR bar ≥ 1.5× its 20-session median (else WARN).
- Ticket (owner types it): price ≤ trigger → `SELL {qty} {sym} MIS LIMIT {entry_limit}`; above → `SELL … MIS SL
  trigger {entry} limit {entry_limit}`; stop line `SL-M BUY {qty} {sym} trigger {stop}`; target `BUY … LIMIT {t1}`;
  "Cancel the entry if not filled by …"; "Square off yourself by 15:10 IST (broker auto square-off charges ₹59+)".
- Shared fill and exit rules mirrored for shorts (fills.py gains side-aware helpers): fill ∈ [entry_limit, entry];
  stop exit ≥ max(Open, stop) on a gap; LOST ⇒ R ≤ 0; every scoreboard invariant and the ambiguous-fill rule apply
  mirrored. Time Machine replays ORB shorts on the stock's own HOSTILE-day history with 2× slippage.
- Gate (per-symbol history is too short: about 60 days of 15-minute bars): an ORB short can be TAKE only if the
  POOLED ORB-short history across the eligible universe, over the available intraday window and HOSTILE days only,
  has ≥ 200 trades and positive expectancy at 2× slippage. Otherwise every short is at most WAIT with "edge not
  proven on the desk's own history yet ({n} trades, {exp}R)" — still overridable as "your call". The pooled figure
  is computed by `python -m nse_backtest.desk shortcheck` (and the page, behind a button) and cached for the day.
- Sizing by rupee risk at the worst fill (entry − entry_limit is the slippage for shorts: worst_risk = stop −
  entry_limit).
- Scoreboard: short rows replayed with the mirrored rules; records and verdicts as for longs, never pooled with longs
  (group key gains side).

## 6. "Cash is a position", measured
- New scoreboard record "Desk sat out (market regime)": every regime-blocked swing verdict that would otherwise have
  been TAKE (no other BLOCK) is logged with `action: "SAT_OUT"` and replayed like a TAKE. Its stats show what sitting
  out saved or cost. Never pooled with Desk picks or Your overrides. Logged at most once per symbol per session.
- "Holding up" watchlist on the plan/Choose tab: up to 10 symbols with the best 60-day relative strength vs Nifty
  among those above their own 50-EMA, labelled "ready for when the regime turns — not a trade".

## 7. Testing and constraints
TDD per task; no network in tests; both stacks; deterministic seeds; every money figure via profile.money(); no
order placement; no LLM. Shorts get the full property tests mirrored from longs. Docs updated (README, SKILL, USAGE).
