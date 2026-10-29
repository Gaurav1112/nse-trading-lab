# Hostile-Market Desk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On any market day the owner sees every candidate trade with levels and money, can tell advice from real problems, can take ORB shorts on hostile days once they are proven, and can see what sitting out costs or saves.

**Architecture:** Keep every computed verdict (`DailyPlan.others`) and rank them; a pure `block_groups` classifier drives the card's colours and override rules; ORB shorts reuse the intraday pipeline with a `side` on the plan and mirrored, shared fill rules; a new `SAT_OUT` audit action feeds a third scoreboard record.

**Tech Stack:** Python 3.13, pandas, numpy, plotly, Streamlit, pytest.

Spec (binding): `docs/superpowers/specs/2026-10-30-hostile-market-desk-design.md`. Inputs: `audit_ux.md`, `research_bear.md` (working notes, not committed).

## Global Constraints

- Gate: `python3.13 -m pytest -o addopts=""` AND the same command in a venv built from requirements.txt, green after
  every task (baseline 1431).
- No order placement; no LLM; no new runtime dependencies; no network in tests.
- Deterministic: `np.random.default_rng(seed)`, seed 19.
- Fail closed: missing data → WARN/BLOCK or "unavailable: {reason}", never a silent PASS or a zero-filled number.
- Money via `profile.money()`; markets never mixed; scoreboard records never pooled.
- The scoreboard must never look better than reality: every invariant in scoreboard.py's docstring holds, mirrored for shorts.
- Existing input hashes of long verdicts must not change (include `side` in the hash only when SHORT).
- Public repo: fictional numbers only. Commit after each task with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never push; never bare `git stash`.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `nse_backtest/desk/daily.py` | modify | keep all verdicts (`others`), check far symbols, split into helpers, per-symbol exception isolation |
| `nse_backtest/desk/session.py` | modify | `merge_candidates(include_others=)`, ranking, plan entry for "Check it" |
| `nse_backtest/desk/verdict.py` | modify | `block_groups`, regime override wording, short tickets |
| `nse_backtest/desk/checks.py` | modify | ban_list WARN for cash longs; short eligibility checks |
| `nse_backtest/desk/models.py` | modify | `Side`, `TradePlan.side`, `DailyPlan.others`, `DailyPlan.holding_up` |
| `nse_backtest/desk/fills.py` | modify | side-aware entry limit and fill helpers |
| `nse_backtest/desk/plan.py`, `timemachine.py`, `intraday.py` | modify | ORB short plan, history, scan |
| `nse_backtest/desk/shortcheck.py` | create | pooled ORB-short edge gate |
| `nse_backtest/desk/scoreboard.py`, `scoreboard_io.py`, `audit.py` | modify | mirrored short replay; `SAT_OUT` record |
| `nse_backtest/desk/strength.py` | create | "holding up" relative-strength watchlist |
| `components/desk_card.py`, `pages/0_Desk.py`, `nse_backtest/desk/cli.py` | modify | UI and CLI |

---

### Task H2: Keep every verdict; rank "Your call"; normal-day bugs

**Files:** modify `daily.py`, `session.py`, `models.py` (DailyPlan), `pages/0_Desk.py` (Choose table), `components/desk_card.py` (quick-check gate), `cli.py` (`plan --all`); tests in `tests/desk/test_daily.py`, `tests/desk/test_session.py`, `tests/test_desk_page_smoke.py`, `tests/desk/test_cli*.py`.

**Interfaces — Produces:**
- `DailyPlan.others: tuple[DeskVerdict, ...] = ()` (LAST field; every computed verdict not in `take`/`watch`).
- `session.merge_candidates(plan, scan, *, include_others: bool = True) -> list[DeskVerdict]`, ranked by `session.candidate_rank(v) -> tuple`.
- `verdict.is_quick(v) -> bool` (True when the verdict came from the first pass: no live quote and not audited); the
  card shows a "Full check" button instead of ticket/override for these.

- [ ] Tests first:
  - a plan of 6 symbols where 4 pass every check with top_take=3 → `take` has 3, the 4th is in `others` with action TAKE (not "not checked");
  - a "far" symbol (price well below the 20-day high) is in `others` with plan entry = 20-day high × 1.001 and full money (card max_loss not None);
  - a symbol whose check raises `RuntimeError` lands in `skipped` with its reason and the other symbols still plan;
  - `others` verdicts are never written to the audit log (tmp audit path stays without them);
  - `candidate_rank` orders TAKE, then WAIT, then advice-only SKIP (blocks only in regime/monte_carlo/setup_history/robustness), then other SKIP; within a group by MC expectancy descending, None last;
  - Choose table shows columns "Exp R", "Expected value", "To entry %" and real numbers for others;
  - opening an `others` card shows "Quick check — run a full check before choosing" and a Full check button, no ticket and no override checkbox; pressing it calls run_check with the plan's entry and replaces the card;
  - "Check it" for a plan symbol passes the plan's entry; Check a Trade's entry field help text says "0 = the desk's own entry (breakout above the 20-day high for swing)";
  - CLI `plan --all` prints the ranked table with a `check SYM --choose` hint per row.
- [ ] Implement: split `daily_plan` into `_first_pass`, `_second_pass`, `_overflow` helpers; catch `Exception` per symbol (record `f"{type(e).__name__}: {e}"`).
- [ ] Gate both stacks; commit `feat(desk): every candidate keeps its numbers — your-call ranking, no lost desk picks`.

### Task H3: Advice vs blocks; regime override wording; data-broken re-check; ban list

**Files:** modify `verdict.py`, `checks.py`, `components/desk_card.py`, `cli.py`; tests `tests/desk/test_verdict_service.py`, `tests/desk/test_checks.py`, `tests/test_desk_page_smoke.py`, `tests/desk/test_choose.py`.

**Interfaces — Produces:** `verdict.block_groups(v: DeskVerdict) -> dict[str, list[Check]]` with keys `"market","odds","data","limits"` exactly per spec §3 (every check name mapped; an unknown name → "limits" and a test that every name `checks.run_checks` can emit is mapped explicitly); `verdict.regime_override_note(v, profile) -> str`.

- [ ] Tests first:
  - every check name emitted by run_checks appears in exactly one group (enumerate names from checks.py);
  - input hash of an unchanged long verdict is identical before/after (pin a fixture hash);
  - card: market group renders as `st.warning` titled "Market says sit out" with the flip conditions; odds as warning; data/limits as `st.error`;
  - a regime-blocked verdict's override header reads "YOUR OVERRIDE — against the market regime (HOSTILE): the desk's held-out longs here averaged −1.61% per trade ≈ ₹{x} on this position" with x = 0.0161 × notional (read the held-out figure from the regime assessment object the tape banner uses; if unavailable, omit the number);
  - a verdict with any "data" BLOCK: no override checkbox; a Re-check button; CLI `check --choose` exits non-zero with "data is broken ({reasons}) — re-check before choosing";
  - ban_list for a swing long → WARN "in the F&O ban — derivatives only; cash delivery allowed" (the SHORT → BLOCK branch and its test are added in H5).
- [ ] Implement; gate both stacks; commit `feat(desk): advice looks like advice — market banner, regime cost on overrides, re-check on broken data`.

### Task H4: Intraday tab scans the universe; every row opens a card

**Files:** modify `pages/0_Desk.py`, `session.py` (scan default universe size); tests in `tests/test_desk_page_smoke.py`.

- [ ] Tests first: the scan button scans `universe(profile)` (all), with a progress indicator; each scan row (TAKE/WAIT/SKIP) has an "Open card" action rendering `render_card` with the H3 override rules; stale rules from the trade-chooser branch still apply.
- [ ] Implement; gate both stacks; commit `feat(desk): intraday scan covers the universe and every row opens a card`.

### Task H5: ORB breakdown shorts (HOSTILE days, F&O stocks, proven edge only)

**Files:** modify `models.py` (`class Side(str, Enum): LONG="long"; SHORT="short"`; `TradePlan.side: Side = Side.LONG` LAST field), `fills.py`, `plan.py`, `timemachine.py`, `checks.py`, `verdict.py`, `sizing.py`, `tradecard.py`, `intraday.py`, `scoreboard.py`, `scoreboard_io.py`, `audit.py`; create `shortcheck.py`; tests `tests/desk/test_fills.py`, `test_plan.py`, `test_timemachine.py`, `test_checks.py`, `test_verdict_service.py`, `test_tradecard.py`, `test_scoreboard.py`, `tests/desk/test_shortcheck.py`.

**Interfaces — Produces:**
- `fills.entry_limit_short(entry, stop, tick) -> float` = round_down_to_tick(entry − min(0.25 × (stop − entry), 0.005 × entry)).
- `fills.stop_limit_fill_short(op, hi, lo, k_start, k_end, trigger, limit) -> tuple[int, float] | None` (mirror: trigger bar = first Low ≤ trigger; Open ≥ limit → fill min(Open, trigger); else rests as sell limit at limit, fill at limit when High ≥ limit).
- `plan.build_plan(..., side=Side.SHORT)` for INTRADAY: entry = OR low, stop = OR mid, target_1 = entry − rr_t1 × R (R = stop − entry), target_2 likewise, entry_limit per above.
- `shortcheck.pooled_short_edge(profile, frames15: dict, regime_by_day: dict[date, str], *, slippage_mult=2.0) -> ShortEdge(n: int, expectancy_r: float | None, proven: bool)`; `proven = n >= 200 and expectancy_r > 0`.
- Checks for SHORT: `short_eligibility` (NSE only; regime HOSTILE; in the F&O list via nse_lists; list unavailable → BLOCK "could not verify F&O eligibility"); `ban_list` BLOCK for shorts; `relative_volume` WARN if the OR bar's volume < 1.5 × 20-session median; `short_edge` WARN-to-WAIT: if not proven, the verdict is at most WAIT with "edge not proven on the desk's own history yet ({n} trades, {exp}R)".
- Tickets per spec §5 (both price branches, cancel line, "Square off yourself by 15:10 IST (broker auto square-off charges ₹59+)").
- Sizing/card: worst_risk for shorts = stop − entry_limit; max_loss at the worst fill; breaches as longs.
- Scoreboard: `Candidate.side`; mirrored replay with every invariant (fill ∈ [entry_limit, entry]; LOST ⇒ R ≤ 0; WON ⇒ exit ≤ target; gap-through-stop exits at max(Open, stop); ambiguous containing-bar fill counts only if it loses, with the cap; quote trust rules); group key includes side; SCOREBOARD_VERSION + 1.
- Input hash: include `side` only when SHORT (pin a long fixture hash unchanged).

- [ ] Tests first for each interface, including mirrored property tests (2,000 seeded random paths per branch) and a test that a long verdict's hash is unchanged.
- [ ] CLI `python -m nse_backtest.desk shortcheck [--market NSE]` prints n, expectancy, proven, and caches it for the day in the data dir; page: button on the Intraday tab.
- [ ] Gate both stacks; commit per sub-part (fills+plan, timemachine+shortcheck, checks+verdict+tickets, sizing+card, scoreboard), each `feat(desk): ORB shorts — …`.

### Task H6: Cash is a position — SAT_OUT record and "holding up" watchlist

**Files:** modify `audit.py`, `daily.py`, `scoreboard.py`, `scoreboard_io.py`, `cli.py`, `pages/0_Desk.py`, `models.py` (`DailyPlan.holding_up: tuple[str, ...] = ()`); create `strength.py`; tests `tests/desk/test_audit.py`, `test_daily.py`, `test_scoreboard*.py`, `tests/desk/test_strength.py`, page tests.

**Interfaces — Produces:** `audit.append_sat_out(v, path=None)` (row = verdict row with `action="SAT_OUT"`; raises unless the only BLOCK is `regime`); scoreboard record `"sat_out"` labelled "Desk sat out (market regime)"; `strength.holding_up(frames: dict[str, DataFrame], index_df, *, n=10) -> list[tuple[str, float]]` (60-day return minus index 60-day return, among symbols with close > own 50-EMA, descending).

- [ ] Tests first: daily_plan logs one SAT_OUT per symbol per session for verdicts whose only BLOCK is regime (and only when audit_path is set); a second plan run the same session doesn't re-log; the scoreboard shows a third section "Desk sat out (market regime)" with its own verdict line, never pooled; holding_up ranks a fixture correctly and excludes symbols below their 50-EMA; the Choose tab shows "Holding up — ready for when the regime turns (not a trade)".
- [ ] Gate both stacks; commit `feat(desk): cash is a position — sat-out record and holding-up watchlist`.

### Task H7: Docs

- [ ] README (Choose a Trade ranking, advice vs blocks, ORB shorts with the edge gate and SEBI notes from research_bear.md: same-day square-off, auction risk, 15:10 self square-off; sat-out record; holding-up list), `.claude/skills/trade-desk/SKILL.md` (`plan --all`, `shortcheck`, shorts never TAKE until proven), USAGE_TOMORROW.md (hostile-day routine). Verify every claim against code; fictional numbers. Gate; commit `docs(desk): hostile-market desk`.

## Self-review notes
Spec §1 → done (fe0eb38); §2 → H2; §3 → H3; §4 → H4; §5 → H5; §6 → H6; §7 → all tasks + H7. Types: `Side` (H5) used by H3's ban_list short branch (H3 adds the WARN for longs; H5 adds the short BLOCK and its test).
