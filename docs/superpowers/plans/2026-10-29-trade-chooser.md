# Trade Chooser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner choose any trade and see its stop-loss, targets, max loss, profit at each target, honest projections and charts, with the desk's verdict as advice and overrides logged and scored separately.

**Architecture:** Four new pure modules (`tradecard`, `projections`, `charts`, `research`) feed a new "Choose a Trade" tab; the intraday entry becomes a stop-limit with one shared fill rule used by the Time Machine and the scoreboard; overrides are a new audit action scored as a separate record.

**Tech Stack:** Python 3.13, pandas, numpy, plotly, Streamlit (AppTest for page tests), pytest.

Spec (binding): `docs/superpowers/specs/2026-10-29-trade-chooser-design.md`.

## Global Constraints

- Gate: `python3.13 -m pytest -o addopts="" -p no:cacheprovider` fully green after every task (baseline 1050 at plan time).
- No order placement anywhere; no LLM call anywhere; no new runtime dependencies (plotly is already a dependency).
- Deterministic: every random draw uses `np.random.default_rng(seed)`, default seed 19.
- Fail closed: missing/failed data → WARN/BLOCK `Check` or an "unavailable: {reason}" section, never a silent PASS, a 0, or an empty-looking chart.
- Money is never mixed across markets; every amount is formatted with `profile.money()`.
- Public repo: tests/examples use fictional capital and prices only; no user data committed.
- Tests make no network calls (tests/desk blocks sockets); subprocess tests use fake scripts.
- Every projection/chart is captioned "range of past behaviour, not a forecast".
- Commit after each task with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never push. Never bare `git stash`.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `nse_backtest/desk/fills.py` | create | `stop_limit_fill`, `entry_limit` — the one stop-limit rule |
| `nse_backtest/desk/plan.py` | modify | intraday plans carry `entry_limit` |
| `nse_backtest/desk/models.py` | modify | `TradePlan.entry_limit` (C1), `Odds.mc_rs` (C4); overrides are audit-only (C2), no new Action |
| `nse_backtest/desk/verdict.py` | modify | stop-limit tickets; override ticket header |
| `nse_backtest/desk/timemachine.py` | modify | intraday history uses `stop_limit_fill`; `monte_carlo` returns per-replay R |
| `nse_backtest/desk/checks.py` | modify | intraday proximity vs `entry_limit` |
| `nse_backtest/desk/intraday.py` | modify | log scan TAKEs |
| `nse_backtest/desk/audit.py` | modify | `append_override`, `entry_limit` in rows |
| `nse_backtest/desk/scoreboard.py`, `scoreboard_io.py` | modify | stop-limit intraday fill; records "desk" / "override"; version 3 |
| `nse_backtest/desk/tradecard.py` | create | `TradeCard`, `build_card` |
| `nse_backtest/desk/projections.py` | create | outcome money, price band, account projection, projected-vs-real |
| `nse_backtest/desk/charts.py` | create | Plotly figure builders |
| `nse_backtest/desk/research.py` | create | skill subprocess runner, US/NSE research |
| `nse_backtest/desk/cli.py` | modify | card text, `check --choose`, `--qty` |
| `pages/0_Desk.py` | modify | "Choose a Trade" tab, card renderer, Scoreboard projected-vs-real |
| `README.md`, `.claude/skills/trade-desk/SKILL.md` | modify | docs |

---

### Task C1: Stop-limit intraday entry

**Files:**
- Create: `nse_backtest/desk/fills.py`, `tests/desk/test_fills.py`
- Modify: `nse_backtest/desk/models.py` (TradePlan), `plan.py`, `verdict.py`, `timemachine.py` (`_intraday_history`), `checks.py` (intraday proximity), `scoreboard.py` (`resolve_intraday`, `_parse_row`), `scoreboard_io.py` (version), `audit.py` (row field)
- Test: `tests/desk/test_fills.py`, plus updates in `test_plan.py`, `test_verdict.py`, `test_timemachine.py`, `test_checks.py`, `test_scoreboard.py`

**Interfaces:**
- Produces: `fills.entry_limit(entry: float, stop: float, tick: float) -> float`; `fills.stop_limit_fill(op, hi, lo, k_start: int, k_end: int, trigger: float, limit: float) -> tuple[int, float] | None`; `TradePlan.entry_limit: float | None = None` (last field, default None so existing constructors keep working); `MarketProfile` tick: NSE 0.05, US 0.01 (add `tick: float` to the profile if absent).

- [ ] **Step 1: Write the failing tests** (`tests/desk/test_fills.py`)

```python
import numpy as np
import pytest
from nse_backtest.desk.fills import entry_limit, stop_limit_fill

def test_entry_limit_caps_at_quarter_R_or_half_percent_rounded_up_to_tick():
    assert entry_limit(100.0, 98.0, 0.05) == 100.50          # min(0.5, 0.5) = 0.5
    assert entry_limit(100.0, 99.6, 0.05) == 100.10          # 0.25R = 0.1
    assert entry_limit(100.0, 90.0, 0.01) == 100.50          # 0.5% cap
    assert entry_limit(100.0, 99.61, 0.05) == 100.10         # 0.0975 rounds UP to 0.10

def _a(*xs): return np.array(xs, dtype=float)

def test_fill_on_trigger_bar_when_open_within_limit():
    # bar0 below trigger, bar1 trades through 100 opening at 99.8 -> fill at trigger 100
    assert stop_limit_fill(_a(99, 99.8), _a(99.5, 100.4), _a(98.8, 99.7), 0, 1, 100.0, 100.5) == (1, 100.0)

def test_open_between_trigger_and_limit_fills_at_open():
    assert stop_limit_fill(_a(100.3), _a(100.6), _a(100.2), 0, 0, 100.0, 100.5) == (0, 100.3)

def test_gap_above_limit_rests_then_fills_at_limit_when_price_returns():
    op, hi, lo = _a(101, 100.9), _a(101.5, 101.0), _a(100.8, 100.4)
    assert stop_limit_fill(op, hi, lo, 0, 1, 100.0, 100.5) == (1, 100.5)

def test_gap_above_limit_never_returns_is_not_filled():
    assert stop_limit_fill(_a(101, 101.2), _a(101.5, 102), _a(100.8, 100.9), 0, 1, 100.0, 100.5) is None

def test_never_triggered():
    assert stop_limit_fill(_a(99, 99.2), _a(99.5, 99.9), _a(98.5, 99), 0, 1, 100.0, 100.5) is None

def test_property_fill_always_between_trigger_and_limit():
    rng = np.random.default_rng(19)
    for _ in range(2000):
        n = 8
        op = 100 + rng.normal(0, 1, n); cl = op + rng.normal(0, 0.6, n)
        hi = np.maximum(op, cl) + rng.uniform(0, 0.8, n); lo = np.minimum(op, cl) - rng.uniform(0, 0.8, n)
        res = stop_limit_fill(op, hi, lo, 0, n - 1, 100.0, 100.5)
        if res is not None:
            k, px = res
            assert 100.0 <= px <= 100.5
            assert hi[: k + 1].max() >= 100.0
```

- [ ] **Step 2: Run** `python3.13 -m pytest -o addopts="" tests/desk/test_fills.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement** `nse_backtest/desk/fills.py`

```python
"""The desk's single stop-limit entry rule (intraday ORB). Used by the Time Machine and the scoreboard."""
from __future__ import annotations

import math

import numpy as np


def entry_limit(entry: float, stop: float, tick: float) -> float:
    """Limit price of the buy stop-limit: entry + min(0.25R, 0.5% of entry), rounded UP to the tick."""
    if not (entry > stop and tick > 0):
        raise ValueError("entry must be above stop and tick positive")
    raw = entry + min(0.25 * (entry - stop), 0.005 * entry)
    return round(math.ceil(round(raw / tick, 9)) * tick, 10)


def stop_limit_fill(op, hi, lo, k_start: int, k_end: int, trigger: float, limit: float
                    ) -> tuple[int, float] | None:
    """(bar index, fill price) of a buy stop-limit over bars k_start..k_end inclusive, or None.
    Trigger bar = first bar with High >= trigger. Open <= limit there -> fill at max(Open, trigger).
    Open above limit -> the order rests as a buy limit at `limit`; fill at `limit` on the first bar
    (trigger bar included) whose Low <= limit. Price never better than the limit in that case."""
    op, hi, lo = (np.asarray(x, dtype=float) for x in (op, hi, lo))
    k = next((i for i in range(k_start, k_end + 1) if hi[i] >= trigger), None)
    if k is None:
        return None
    if op[k] <= limit:
        return k, float(max(op[k], trigger))
    j = next((i for i in range(k, k_end + 1) if lo[i] <= limit), None)
    return None if j is None else (j, float(limit))
```

- [ ] **Step 4: Run** the fills tests → PASS.

- [ ] **Step 5: Wire it in (tests first for each, then code):**
  1. `models.TradePlan`: add `entry_limit: float | None = None` as the LAST field. `markets.MarketProfile`: `tick` (NSE 0.05, US 0.01).
  2. `plan.build_plan` INTRADAY: set `entry_limit=fills.entry_limit(entry, stop, profile.tick)` via `dataclasses.replace` at the end; swing stays None. Test: an intraday plan has `entry_limit` = formula; swing has None.
  3. `verdict.ticket_lines` intraday: NSE first line `f"BUY {qty} {sym} MIS SL trigger {plan.entry:.2f} limit {plan.entry_limit:.2f}"`; US intraday `f"BUY {qty} {sym} STOP-LIMIT stop {plan.entry:.2f} limit {plan.entry_limit:.2f} — bracket: stop-loss {plan.stop:.2f}, take-profit {plan.target_1:.2f}, DAY"`. Swing lines unchanged. Update existing ticket tests.
  4. `timemachine._intraday_history`: replace `entry = float(max(op[k], or_high))` and the breakout search with `stop_limit_fill(op, hi, lo, first_in_window, last_in_window, or_high, entry_limit(or_high, stop, prof.tick))` restricted to bars inside `[no_entry_before, no_entry_after]` (stop = OR mid is known before the fill). R uses the actual fill price. On the fill bar only the stop counts, exit `min(op[k], stop)` (keep the invariant LOST ≤ 0R). Test: a history day that gaps above the limit and never returns is not a trade; one that returns fills at the limit.
  5. `checks.py` intraday price proximity: where TAKE currently allows the live price up to 1% above entry, intraday now requires `price <= plan.entry_limit`, else WAIT `f"Price {m(price)} is above the stop-limit's limit {m(limit)} — the order would not fill"`. Swing unchanged. Test both.
  6. `audit.append_verdict`: add `"entry_limit": p.entry_limit` to the row.
  7. `scoreboard`: `Candidate.entry_limit: float | None` (from the row; missing → `entry_limit(entry, stop, tick)`); `resolve_intraday` fills with `stop_limit_fill` over bars after created_at and before flat-by (trigger = entry, limit = entry_limit); stop on the fill bar exits at `min(op[k], stop)`. `scoreboard_io.SCOREBOARD_VERSION = 3`. Update the intraday scoreboard tests; keep the C1/property invariants from the earlier round (LOST ⇒ exit ≤ stop, realised ≤ 0 before costs; WON ⇒ exit ≥ target; fill ∈ [entry, entry_limit]).

- [ ] **Step 6: Gate** → green. **Step 7: Commit** `feat(desk): intraday entry is a stop-limit — one fill rule for ticket, odds and scoreboard`.

---

### Task C2: Log scan TAKEs and owner overrides; two scoreboard records

**Files:**
- Modify: `nse_backtest/desk/intraday.py`, `audit.py`, `scoreboard.py`, `scoreboard_io.py`, `cli.py` (scoreboard output grouping), `pages/0_Desk.py` (Scoreboard tab grouping)
- Test: `tests/desk/test_intraday.py`, `tests/desk/test_audit.py`, `tests/desk/test_scoreboard.py`, `tests/desk/test_scoreboard_io.py`

**Interfaces:**
- Produces: `audit.append_override(v: DeskVerdict, path: Path | None = None) -> Path` (row = `append_verdict` row with `action="OVERRIDE"`, `desk_action=v.action.value`, `chosen_by="owner"`); `scoreboard.Candidate.record: str` ("desk" | "override"); `ScoreStats.record: str` (new LAST field, default "desk"); `summarize` groups by (record, market, mode).

- [ ] Tests first:
  - `orb_scan(..., audit_path=tmp)` logs exactly the TAKE verdicts (2 TAKE + 1 WAIT → 2 rows); a scan raising for one symbol still logs the others. Add `audit_path: Path | None = None` to `orb_scan`; it calls `check_trade(..., audit=False)` and then `append_verdict` for TAKEs only.
  - `append_override` on a SKIP verdict writes `action == "OVERRIDE"`, `desk_action == "SKIP"`, `chosen_by == "owner"`; on a TAKE verdict raises `ValueError("TAKE verdicts are logged as desk picks, not overrides")`.
  - `load_candidates`: TAKE rows → record "desk"; OVERRIDE rows → record "override"; other actions ignored. Dedupe key gains the record (a desk TAKE and an owner override of the same symbol/session are two candidates).
  - `summarize`: stats never pool records; an override-only list yields one ScoreStats with record "override".
  - CLI `scoreboard` prints a "Desk picks" section and a "Your overrides" section (each with its own verdict line first); page Scoreboard tab shows the two records under separate subheaders.
- [ ] Implement, gate, commit `feat(desk): log scan TAKEs; owner overrides are a separate scoreboard record`.

---

### Task C3: Trade card (money math, breaches, ticket gating)

**Files:**
- Create: `nse_backtest/desk/tradecard.py`, `tests/desk/test_tradecard.py`

**Interfaces:**
- Consumes: `DeskVerdict`, `Limits`, `MarketProfile`, `costs.round_trip_cost`, `verdict.ticket_lines`.
- Produces:

```python
@dataclass(frozen=True)
class TradeCard:
    verdict: DeskVerdict
    qty: int
    qty_is_override: bool
    entry: float; stop: float; target_1: float; target_2: float; entry_limit: float | None
    risk_per_share: float
    cost: float                 # round trip at T1, qty
    max_loss: float             # qty*(entry-stop) + cost  (positive number = money lost)
    profit_t1: float            # qty*(t1-entry) - cost
    profit_t2: float            # qty*(t2-entry) - cost (cost computed at T2 exit)
    rr_t1: float
    expected_value: float | None   # mc_expectancy_r * qty * risk_per_share; None without odds
    notional: float
    pct_capital_at_risk: float
    pct_capital_deployed: float
    breaches: tuple[str, ...]   # human-readable limit breaches caused by a qty override
    gap_note: str               # "A gap through the stop can lose more than this."
    ticket: tuple[str, ...]     # () unless allowed (TAKE, or override confirmed)
    ticket_header: str          # "" for TAKE; "YOUR OVERRIDE — the desk said SKIP" for overrides
    reasons: tuple[str, ...]    # "BLOCK name — reason" / "WARN name — reason" for non-TAKE

def build_card(v: DeskVerdict, profile: MarketProfile, limits: Limits, *, qty: int | None = None,
               override_confirmed: bool = False) -> TradeCard
```

- [ ] Tests first (fictional numbers): NSE swing TAKE entry 100, stop 95, T1 110, T2 115, qty 10 → max_loss = 50 + cost, profit_t1 = 100 − cost, profit_t2 = 150 − cost_t2, rr_t1 2.0; expected_value = mc_expectancy_r × 50 (None when odds None); qty override 40 with capital 10,000 and risk 1% → breaches lists "risk per trade 2.0% of capital (limit 1.0%)" (and max position if notional > 25%); qty 0, −1 or 2.5 → ValueError; SKIP verdict without confirmation → ticket (), reasons non-empty; with `override_confirmed=True` → ticket == `ticket_lines(plan, sizing-with-qty, profile)` and header "YOUR OVERRIDE — the desk said SKIP"; TAKE → ticket shown, header "". US verdict money via `profile.money` is the caller's job; the card stores numbers only.
- [ ] Implement (pure; use `dataclasses.replace(v.sizing, qty=...)` for the override ticket), gate, commit `feat(desk): trade card — max loss, profit at T1/T2, breaches, override-gated ticket`.

---

### Task C4: Projections engine

**Files:**
- Modify: `nse_backtest/desk/models.py` (`Odds.mc_rs: tuple[float, ...] = ()` LAST field), `timemachine.py` (`monte_carlo` returns rs too, via a new `monte_carlo_rs`; `odds()` fills `mc_rs` capped at 10,000)
- Create: `nse_backtest/desk/projections.py`, `tests/desk/test_projections.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class OutcomeMoney:
    pnl: tuple[float, ...]          # per replay, money
    p5: float; p25: float; median: float; p75: float; p95: float
    p_target: float; p_stop: float; p_time: float

def outcome_money(card: TradeCard) -> OutcomeMoney | None          # None when odds or mc_rs missing

@dataclass(frozen=True)
class PriceBand:
    start: pd.Timestamp; steps: int
    p5: tuple[float, ...]; p25: tuple[float, ...]; p50: tuple[float, ...]; p75: tuple[float, ...]; p95: tuple[float, ...]

def price_band(closes: pd.Series, steps: int, *, paths: int = 2000, block: int = 5, seed: int = 19) -> PriceBand | None

@dataclass(frozen=True)
class AccountProjection:
    n_trades: int; start_capital: float
    p5: tuple[float, ...]; p50: tuple[float, ...]; p95: tuple[float, ...]   # capital after trade i (len n+1, index 0 = start)
    p_dd_10: float; p_finish_down: float; source: str   # "Monte Carlo replays" | "setup history"

def account_projection(capital: float, risk_amount: float, rs, n_trades: int, *, paths: int = 2000, seed: int = 19,
                       source: str = "Monte Carlo replays") -> AccountProjection | None

def projected_vs_real(outcomes) -> tuple[list[float], list[float], list[tuple[float, float]], int]
    # (cumulative projected R, cumulative realised R, [(projected, realised)] per trade, n_without_prediction)
```

- [ ] Tests first: `monte_carlo` summary values unchanged by the refactor (compare to a stored call with seed 19 on fixed ratios); `odds().mc_rs` length = min(replays, 10,000) and its mean ≈ `mc_expectancy_r` (exact equality when uncapped); `outcome_money` percentiles on a known card equal `np.percentile(R × qty × R$)`; None without odds; `price_band` envelopes ordered p5 ≤ p25 ≤ p50 ≤ p75 ≤ p95 at every step, length = steps, deterministic for a seed, None for < 30 closes; `account_projection` on rs = [+2, −1] × equal weight: p50 final ≈ start + n×0.5×risk; deterministic; p_finish_down equals the share of paths below start; None for empty rs or non-positive capital/risk; `projected_vs_real` skips outcomes without mc prediction from the projected line and counts them.
- [ ] Implement (numpy vectorised; block bootstrap of log returns for the band), gate, commit `feat(desk): projections — outcome money, price band, account projection, projected vs real`.

---

### Task C5: Charts

**Files:**
- Create: `nse_backtest/desk/charts.py`, `tests/desk/test_charts.py`

**Interfaces:**
- Consumes: `TradeCard`, `OutcomeMoney`, `PriceBand`, `AccountProjection`, `projected_vs_real` output, `MarketProfile`.
- Produces: `price_chart(bars: pd.DataFrame, card, band: PriceBand | None, profile) -> go.Figure`; `outcome_chart(om: OutcomeMoney, card, profile) -> go.Figure`; `account_chart(ap: AccountProjection, profile) -> go.Figure`; `projected_vs_real_chart(cum_proj, cum_real, pairs) -> go.Figure`. Each figure's layout title/annotation includes "range of past behaviour, not a forecast" (except projected-vs-real, which says "projected vs what happened").

- [ ] Tests first: price chart has a Candlestick trace with len(bars) points; horizontal shapes at entry, stop, T1, T2 (and entry_limit for intraday) with those exact y values and labels; band adds filled traces whose y arrays equal the band's p5/p95 and p25/p75; band None → no band traces and an annotation "range band unavailable"; outcome chart has a Histogram of `om.pnl` and vertical lines at p5, median, p95; account chart has p5/p50/p95 lines of length n+1 and a text annotation with p_dd_10 and p_finish_down as percentages; money tick labels use the market currency symbol (₹ or $).
- [ ] Implement with `plotly.graph_objects`, gate, commit `feat(desk): charts — price with levels and range band, outcome histogram, account projection`.

---

### Task C6: Research panel adapters

**Files:**
- Create: `nse_backtest/desk/research.py`, `tests/desk/test_research.py`, `tests/desk/fixtures/fake_skill.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class ResearchItem: title: str; url: str | None; when: str | None; kind: str     # "headline" | "8-K" | "earnings" | "dividend" | "split"
@dataclass(frozen=True)
class Research: source: str; fetched_at: str; items: tuple[ResearchItem, ...]; notes: tuple[str, ...]
@dataclass(frozen=True)
class Unavailable: reason: str

def run_skill(script: Path, args: list[str], *, timeout: float = 10.0, env: dict | None = None) -> dict | Unavailable
def us_research(symbol: str, *, runner=run_skill) -> Research | Unavailable
def nse_research(symbol: str, *, earnings=None, actions=None) -> Research | Unavailable
```

- [ ] Tests first (no network): `run_skill` with the fake script → parsed dict; fake script that sleeps → Unavailable("timed out after 10s"); exits 1 → Unavailable with its stderr's last line (no full paths); prints non-JSON → Unavailable("bad output"); invalid symbol ("A B", "../x") → Unavailable before any subprocess; `run_skill` uses `[sys.executable, "-I", str(script), *args]`, a fresh temp cwd, and an env containing only PATH, HOME, SEC_USER_AGENT (if set); `us_research` without SEC_USER_AGENT adds the note "SEC data unavailable: set SEC_USER_AGENT" and still returns headlines from a fake runner; maps news JSON + facts JSON into items (use the real field names — read `.claude/skills/trader-news/news.py` `--json` output and `trader-analyst/facts.py --json` to map them; record the mapping in a module comment); `nse_research` maps an injected earnings date and actions frame to items.
- [ ] Isolation test: AST-scan `nse_backtest/desk/{checks,verdict,service,plan,sizing,timemachine,daily,intraday,scoreboard,scoreboard_io,tradecard,projections}.py` → none imports `research`; `research.py` imports from `nse_backtest.desk` only `markets`.
- [ ] Update `.claude/skills/VENDORED.md` "Rule" section: the skills may be run by `desk/research.py` for a display-only panel; still never inputs to a verdict (enforced by the isolation test).
- [ ] Implement, gate, commit `feat(desk): research panel — US news/8-K/earnings via vendored skills, NSE earnings and actions; display only`.

---

### Task C7: "Choose a Trade" tab, card UI, CLI

**Files:**
- Modify: `pages/0_Desk.py`, `nse_backtest/desk/cli.py`, `nse_backtest/desk/session.py` (helper to gather candidates)
- Test: `tests/test_desk_page_smoke.py` (new tests), `tests/desk/test_cli*.py`

**Interfaces:**
- Consumes: `build_card`, `outcome_money`, `price_band`, `account_projection`, chart builders, `us_research`/`nse_research`, `append_override`, existing `run_daily_plan`, `run_intraday`, `run_check`.
- Produces: `session.choose_candidates(profile, limits, on_warning) -> list[DeskVerdict]` (plan verdicts incl. WATCH/WAIT/SKIP + today's intraday scan verdicts, deduped by symbol+mode, sorted TAKE first then by expectancy); page functions `_render_card(v, profile, limits, key)`.

- [ ] Page tests first (AppTest, fakes for session functions, no network): tab "Choose a Trade" is the FIRST tab; the candidates table shows columns Symbol, Mode, Desk says, Entry, Stop, T1, T2, Qty, Max loss, Profit T1, Win odds (money in ₹ for NSE); selecting a row renders the card with metrics Max loss / Profit at T1 / Profit at T2 / Risk:Reward / Expected value; a SKIP row shows its reasons in an error box and no ticket until the "I choose this anyway" checkbox is ticked, after which the ticket and the "YOUR OVERRIDE" header appear and `append_override` is called once (not on every rerun — key the log on (input_hash, qty)); editing qty above the sized qty shows the breaches in red; each section that raises shows its own `st.error` and the rest still render; research panel shows Unavailable reasons; charts are `st.plotly_chart` calls with the figures from C5; projections are captioned "range of past behaviour, not a forecast"; Account projection has an N selector (10/20/50, default 20).
- [ ] "Check a Trade" renders the same `_render_card` for its verdict (entry/stop/target inputs already exist; add a qty input).
- [ ] CLI: `check` output gains a "Money" block (max loss, profit at T1/T2, R:R, expected value, breaches) from `build_card`; `check --qty N` overrides qty; `check --choose` on a WAIT/SKIP prints the override ticket under "YOUR OVERRIDE — the desk said …" and logs `append_override`; on a TAKE `--choose` is a no-op note. Tests with monkeypatched session.
- [ ] Implement, gate, commit `feat(desk): Choose a Trade tab — every candidate with money, charts, research and your-call tickets`.

---

### Task C8: Projected vs real on the Scoreboard

**Files:**
- Modify: `pages/0_Desk.py` (Scoreboard tab), `nse_backtest/desk/cli.py` (scoreboard text: one line "Projected {x}R vs realised {y}R over n trades")
- Test: page and CLI tests

- [ ] Tests first: per record, the Scoreboard tab shows `projected_vs_real_chart` when ≥ 1 resolved trade has a prediction, else an info line "No projections to compare yet"; caption counts trades without predictions; CLI prints the projected/realised line per record.
- [ ] Implement, gate, commit `feat(desk): scoreboard shows projected vs realised R`.

---

### Task C9: Docs and test-data hardening

**Files:**
- Modify: `README.md` (Choose a Trade, money definitions incl. the gap note, stop-limit ticket, overrides record, projections caveat, research panel + SEC_USER_AGENT), `.claude/skills/trade-desk/SKILL.md` (`check --choose/--qty`, report money first), `USAGE_TOMORROW.md` (morning routine uses Choose a Trade), `tests/test_desk_page_smoke.py` (its synthetic daily bars end "yesterday" → end on the last completed NSE session, as `tests/desk/test_cli_loaders.py` now does)

- [ ] Verify every doc claim against code; fictional numbers only. Gate on the local stack AND on a venv from requirements.txt. Commit `docs(desk): trade chooser, stop-limit tickets, overrides, projections, research panel`.

---

## Self-review notes

- Spec §1 → C7; §2 → C3 (+C7 UI); §3 → C1; §4 → C2 (+C7 override logging); §5.1–5.3 → C4/C5/C7; §5.4 → C4/C5/C8; §6 → C6 (+C7 panel); §7 → C7; §8 → every task; §9 respected.
- Types: `entry_limit` (C1) used by C3 card and C7 UI; `Odds.mc_rs` (C4) used by `outcome_money` and `account_projection`; `ScoreStats.record` (C2) used by C8.
