# Trade Desk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One dashboard (NSE + US) that turns any stock into a complete, sized, stress-tested TAKE / WAIT / SKIP decision, plus a daily plan, a position review and an intraday ORB scan.

**Architecture:** New pure package `nse_backtest/desk/` (no Streamlit, no network) composed by a thin `service.py`; network only in `desk/loaders.py` and `desk/nse_lists.py`; UI in `pages/0_Desk.py`; CLI `python -m nse_backtest.desk`; Claude skill wraps the CLI.

**Tech Stack:** Python 3.13, pandas, numpy, Streamlit (AppTest for smoke), pytest. Spec: `docs/superpowers/specs/2026-10-29-trade-desk-design.md`.

## Global Constraints

- Gate: `python3.13 -m pytest -q --ignore=tests/pipeline` must be green after every task (pipeline tests need `fyers_apiv3`, not installed locally).
- No new runtime dependencies. No GPL/AGPL code copied or imported. Code ported from day-trader-skill (MIT) carries a docstring line `Adapted from github.com/Jakeschincariol/day-trader-skill (MIT).`
- Deterministic: every random draw uses `np.random.default_rng(seed)`; default seed 19.
- No order placement anywhere. No LLM call anywhere.
- Missing/failed data becomes a WARN or BLOCK `Check`, never a silent PASS.
- Public repo: tests/examples use fictional capital values only; no user data committed.
- Money is never mixed across markets; every amount is formatted with `profile.money()`.
- Commit after each task with a `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` trailer. Do not push.

## File map

| File | Responsibility |
|---|---|
| `nse_backtest/desk/__init__.py` | Re-exports public API |
| `nse_backtest/desk/markets.py` | `MarketProfile`, `NSE`, `US`, `get_profile`, `universe` |
| `nse_backtest/desk/costs.py` | `round_trip_cost` per market/mode |
| `nse_backtest/desk/models.py` | Dataclasses + enums |
| `nse_backtest/desk/plan.py` | `build_plan`, `atr`, swing/intraday geometry |
| `nse_backtest/desk/sizing.py` | `size_position` |
| `nse_backtest/desk/timemachine.py` | Monte Carlo, setup history, shuffle, noise → `Odds` |
| `nse_backtest/desk/nse_lists.py` | F&O ban list fetch/parse/cache |
| `nse_backtest/desk/checks.py` | `CheckContext`, `run_checks`, `stoploss_streak` |
| `nse_backtest/desk/verdict.py` | `decide`, tickets, input hash |
| `nse_backtest/desk/audit.py` | append-only JSONL audit |
| `nse_backtest/desk/service.py` | `check_trade` orchestration |
| `nse_backtest/desk/positions.py` | `review_positions` |
| `nse_backtest/desk/daily.py` | `daily_plan`, `flip_conditions` |
| `nse_backtest/desk/intraday.py` | `orb_scan` |
| `nse_backtest/desk/loaders.py` | yfinance/state/settings I/O |
| `nse_backtest/desk/cli.py`, `__main__.py` | CLI |
| `pages/0_Desk.py` | Streamlit dashboard, 4 tabs + market switch + settings |
| `.claude/skills/trade-desk/SKILL.md` | Claude skill over the CLI |
| `.mcp.json` | hosted read-only Kite MCP for Claude sessions |
| `tests/desk/test_*.py` | one test file per module |

---

### Task 1: Markets, costs, models

**Files:** Create `nse_backtest/desk/{__init__,markets,costs,models}.py`, `tests/desk/__init__.py`, `tests/desk/test_markets_costs.py`. Modify `pyproject.toml` `[tool.setuptools] packages` to add `"nse_backtest.desk"`. Modify spec §2a row "round-trip cost" US cells to read `SEC fee 0.00278% of sell + FINRA TAF $0.000166/share (max $8.30) + spread`.

**Interfaces — Produces:**

```python
# markets.py
@dataclass(frozen=True)
class MarketProfile:
    code: str; currency: str; tz: str
    session_open: time; session_close: time
    no_entry_before: time; no_entry_after: time; flat_by: time
    regime_index: str; exchange: str          # "NS" for NSE, "" for US (fetch_nse exchange arg)
    min_turnover: float                        # local currency, 20d median Close*Volume
    has_ban_list: bool
    def yf_symbol(self, symbol: str) -> str    # "reliance"->"RELIANCE.NS"; "^NSEI" unchanged; already-suffixed unchanged; US unchanged upper
    def money(self, x: float) -> str           # NSE.money(1234.5) == "₹1,234.50"
NSE = MarketProfile("NSE", "₹", "Asia/Kolkata", time(9,15), time(15,30), time(9,30), time(14,30), time(15,15), "^NSEI", "NS", 5e7, True)
US  = MarketProfile("US",  "$", "America/New_York", time(9,30), time(16,0), time(9,45), time(15,0), time(15,55), "^GSPC", "", 2e7, False)
def get_profile(code: str) -> MarketProfile   # case-insensitive; ValueError otherwise
US_LARGE_CAP_50: list[str]                     # 50 unique liquid US large caps (AAPL, MSFT, NVDA, AMZN, GOOGL, META, BRK-B, ...)
def universe(profile) -> list[str]             # NSE -> list(NIFTY50_SYMBOLS); US -> list(US_LARGE_CAP_50)

# models.py
class Status(str, Enum): PASS="PASS"; WARN="WARN"; BLOCK="BLOCK"
class Action(str, Enum): TAKE="TAKE"; WAIT="WAIT"; SKIP="SKIP"
class Mode(str, Enum): SWING="swing"; INTRADAY="intraday"

@dataclass(frozen=True)
class Limits:
    capital: float
    risk_per_trade_pct: float = 1.0
    max_position_pct: float = 25.0
    cash_buffer_pct: float = 10.0
    daily_loss_limit_pct: float = 2.0
    min_rr: float = 1.5
    pdt_guard: bool = False

@dataclass(frozen=True)
class TradePlan:
    symbol: str; market: str; mode: Mode
    entry: float; stop: float; target_1: float; target_2: float
    atr: float; max_hold_bars: int
    last_close: float; last_bar: str           # ISO timestamp of last bar used
    stop_basis: str                            # "2×ATR" | "10-bar swing low" | "1×ATR floor" | "user" | "opening-range mid"
    @property
    def risk_per_share(self) -> float: return self.entry - self.stop
    @property
    def rr_t1(self) -> float: return (self.target_1 - self.entry) / self.risk_per_share

@dataclass(frozen=True)
class Sizing:
    qty: int; binding_limit: str; risk_amount: float; notional: float
    costs: float; size_halved: bool; note: str

@dataclass(frozen=True)
class Odds:
    replays: int; seed: int
    p_target: float; p_stop: float; p_time: float; mc_expectancy_r: float
    history_n: int; history_win_pct: float | None; history_expectancy_r: float | None
    history_rs: tuple[float, ...]
    dd_p5_r: float | None; dd_p50_r: float | None; dd_p95_r: float | None; p_5_loss_streak: float | None
    noise_expectancy_min: float | None; noise_expectancy_max: float | None; noise_negative_frac: float | None

@dataclass(frozen=True)
class Check:
    name: str; status: Status; reason: str

@dataclass(frozen=True)
class DeskVerdict:
    action: Action; headline: str
    plan: TradePlan; sizing: Sizing; odds: Odds | None; checks: tuple[Check, ...]
    ticket: tuple[str, ...]; trigger: float | None
    current_price: float; input_hash: str; created_at: str
    def blocks(self) -> list[Check]: return [c for c in self.checks if c.status is Status.BLOCK]
    def warns(self)  -> list[Check]: return [c for c in self.checks if c.status is Status.WARN]

# costs.py
def round_trip_cost(profile: MarketProfile, mode: Mode, entry: float, exit_price: float,
                    qty: int, spread_per_side: float = 0.00075) -> float   # money, >= 0; qty<=0 -> 0.0
```

Cost formulas (implement exactly):
- NSE swing: `picker_replay._delivery_costs_pct(entry, exit_price, qty, spread_per_side) / 100 * exit_price * qty`.
- NSE intraday: `buy=entry*qty; sell=exit_price*qty; brk=min(20, 0.0003*buy)+min(20, 0.0003*sell); stt=0.00025*sell; stamp=0.00003*buy; txn=(buy+sell)*(0.0000297+0.000001+0.0000001); gst=0.18*(brk+txn); spread=(buy+sell)*spread_per_side; return brk+stt+stamp+txn+gst+spread`.
- US (both modes): `sec=0.0000278*sell; taf=min(0.000166*qty, 8.30); spread=(buy+sell)*spread_per_side; return sec+taf+spread`.

- [ ] **Step 1: Write the failing tests** — `tests/desk/test_markets_costs.py`:

```python
import pytest
from datetime import time
from nse_backtest.desk.markets import NSE, US, get_profile, universe, US_LARGE_CAP_50
from nse_backtest.desk.costs import round_trip_cost
from nse_backtest.desk.models import Mode, TradePlan
from nse_backtest.data import NIFTY50_SYMBOLS
from nse_backtest.picker_replay import _delivery_costs_pct


def test_get_profile_case_insensitive_and_rejects_unknown():
    assert get_profile("nse") is NSE and get_profile("US") is US
    with pytest.raises(ValueError):
        get_profile("LSE")


@pytest.mark.parametrize("prof,raw,want", [
    (NSE, "reliance", "RELIANCE.NS"), (NSE, "INFY.NS", "INFY.NS"),
    (NSE, "^NSEI", "^NSEI"), (US, "aapl", "AAPL"), (US, "BRK-B", "BRK-B"),
])
def test_yf_symbol(prof, raw, want):
    assert prof.yf_symbol(raw) == want


def test_money_uses_market_currency():
    assert NSE.money(1234.5) == "₹1,234.50"
    assert US.money(-3) == "-$3.00"


def test_sessions():
    assert NSE.session_open == time(9, 15) and US.flat_by == time(15, 55)


def test_universe():
    assert universe(NSE) == list(NIFTY50_SYMBOLS)
    assert len(US_LARGE_CAP_50) == 50 == len(set(US_LARGE_CAP_50))
    assert universe(US) == US_LARGE_CAP_50


def test_nse_swing_cost_matches_existing_delivery_model():
    got = round_trip_cost(NSE, Mode.SWING, 100.0, 104.0, 50, 0.00075)
    assert got == pytest.approx(_delivery_costs_pct(100.0, 104.0, 50, 0.00075) / 100 * 104.0 * 50)


def test_nse_intraday_cost_formula():
    buy, sell = 100.0 * 100, 101.0 * 100
    brk = min(20, 0.0003 * buy) + min(20, 0.0003 * sell)
    txn = (buy + sell) * (0.0000297 + 0.000001 + 0.0000001)
    want = brk + 0.00025 * sell + 0.00003 * buy + txn + 0.18 * (brk + txn) + (buy + sell) * 0.00075
    assert round_trip_cost(NSE, Mode.INTRADAY, 100.0, 101.0, 100) == pytest.approx(want)


def test_us_cost_formula_caps_taf():
    buy, sell = 50.0 * 100_000, 51.0 * 100_000
    want = 0.0000278 * sell + 8.30 + (buy + sell) * 0.0003
    assert round_trip_cost(US, Mode.SWING, 50.0, 51.0, 100_000, 0.0003) == pytest.approx(want)


def test_zero_qty_costs_nothing():
    assert round_trip_cost(NSE, Mode.SWING, 100, 100, 0) == 0.0


def test_tradeplan_properties():
    p = TradePlan("X", "NSE", Mode.SWING, 100, 98, 103, 106, 2, 15, 100, "2026-10-28", "user")
    assert p.risk_per_share == 2 and p.rr_t1 == 1.5
```

- [ ] **Step 2:** `python3.13 -m pytest tests/desk/test_markets_costs.py -q` → FAIL (ModuleNotFoundError).
- [ ] **Step 3:** Implement the four modules exactly per the interface block. `money`: `f"{'-' if x < 0 else ''}{self.currency}{abs(x):,.2f}"`. `__init__.py` re-exports `NSE, US, get_profile, Mode, Status, Action, Limits`.
- [ ] **Step 4:** Re-run → PASS. Run full gate.
- [ ] **Step 5:** Commit `feat(desk): market profiles, cost model, desk data models`.

---

### Task 2: Trade plan geometry

**Files:** Create `nse_backtest/desk/plan.py`, `tests/desk/test_plan.py`.

**Interfaces:** Consumes Task 1. Produces:

```python
def atr(df: pd.DataFrame, period: int = 14) -> float
    # TR = max(H-L, |H-prevC|, |L-prevC|) (first bar: H-L); simple mean of the last `period` TRs
    # (or of all bars if fewer). Raises ValueError on empty df.
def atr_series(df, period=14) -> pd.Series   # rolling simple mean, same TR (used by timemachine)
def swing_stop(entry: float, atr_value: float, lowest_low_10: float) -> tuple[float, str]
    # cand_atr = entry - 2*atr; cand_low = lowest_low_10*0.999; stop = max(both);
    # basis "2×ATR" if cand_atr >= cand_low else "10-bar swing low";
    # if entry - stop < atr: stop = entry - atr, basis "1×ATR floor"
def build_plan(df, symbol, mode: Mode, profile: MarketProfile, *, entry=None, stop=None,
               target=None, max_hold_bars: int = 15) -> TradePlan
```

Rules:
- Validate: df has Open/High/Low/Close; swing needs ≥ 20 rows else `ValueError("need at least 20 bars")`.
- Swing: `entry = entry or last close`; stop = user stop (basis `"user"`) or `swing_stop(entry, atr(df), df["Low"].tail(10).min())`; `stop >= entry` → `ValueError("stop must be below entry")`. `R = entry - stop`; `T1 = target or entry + 1.5R`; `target <= entry` → ValueError; `T2 = max(T1, entry + 3R)`.
- Intraday (df = 15-min bars, tz-naive local exchange time): session = rows whose date == last row's date; opening range = first row of session. `entry = entry or OR high`; `stop = stop or (OR high + OR low)/2` (basis `"opening-range mid"`); `T1 = target or entry + 1R`; `T2 = max(T1, entry + 2R)`; `max_hold_bars` = number of 15-min bars from the last bar's time to `profile.flat_by` (min 1). ATR = `atr(df)` on the 15-min frame. Needs ≥ 1 session row.
- `last_bar = df.index[-1].isoformat()`, `last_close = float(df.Close.iloc[-1])`, `market = profile.code`.

- [ ] **Step 1: Failing tests** — `tests/desk/test_plan.py`:

```python
import numpy as np, pandas as pd, pytest
from nse_backtest.desk.plan import atr, build_plan, swing_stop
from nse_backtest.desk.models import Mode
from nse_backtest.desk.markets import NSE


def flat(n=30):
    idx = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1e6}, index=idx)


def test_atr_constant_range():
    assert atr(flat()) == pytest.approx(2.0)


def test_swing_stop_clamped_to_one_atr_floor():
    p = build_plan(flat(), "X", Mode.SWING, NSE)
    assert p.entry == 100 and p.stop == pytest.approx(98.0) and p.stop_basis == "1×ATR floor"
    assert p.target_1 == pytest.approx(103.0) and p.target_2 == pytest.approx(106.0)
    assert p.market == "NSE" and p.max_hold_bars == 15


def test_swing_stop_uses_2atr_when_swing_low_far():
    df = flat()
    df.iloc[-10, df.columns.get_loc("Low")] = 80.0
    a = (13 * 2 + 21) / 14
    p = build_plan(df, "X", Mode.SWING, NSE)
    assert p.atr == pytest.approx(a)
    assert p.stop == pytest.approx(100 - 2 * a) and p.stop_basis == "2×ATR"


def test_swing_stop_helper_prefers_higher_stop():
    assert swing_stop(100, 5, 95.0) == (pytest.approx(94.905), "10-bar swing low")


def test_user_overrides():
    p = build_plan(flat(), "X", Mode.SWING, NSE, entry=100, stop=95, target=110)
    assert (p.stop, p.stop_basis, p.target_1, p.target_2) == (95, "user", 110, 115)


@pytest.mark.parametrize("kw", [dict(stop=100), dict(stop=101), dict(target=99)])
def test_rejects_bad_geometry(kw):
    with pytest.raises(ValueError):
        build_plan(flat(), "X", Mode.SWING, NSE, entry=100, **kw)


def test_rejects_short_history():
    with pytest.raises(ValueError):
        build_plan(flat(10), "X", Mode.SWING, NSE)


def test_intraday_opening_range_plan():
    idx = pd.date_range("2026-10-28 09:15", periods=6, freq="15min")
    df = pd.DataFrame({"Open": [100, 101, 102, 102, 103, 103], "High": [102, 103, 103, 104, 104, 104],
                       "Low": [99, 100, 101, 101, 102, 102], "Close": [101, 102, 102, 103, 103, 104],
                       "Volume": 1e5}, index=idx)
    p = build_plan(df, "X", Mode.INTRADAY, NSE)
    assert p.entry == 102 and p.stop == pytest.approx(100.5) and p.stop_basis == "opening-range mid"
    assert p.target_1 == pytest.approx(103.5) and p.target_2 == pytest.approx(105.0)
    # last bar 10:30 -> flat by 15:15 = 19 bars of 15 min
    assert p.max_hold_bars == 19
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS + gate. **Step 5:** Commit `feat(desk): trade plan geometry for swing and intraday ORB`.

---

### Task 3: Position sizing

**Files:** Create `nse_backtest/desk/sizing.py`, `tests/desk/test_sizing.py`.

**Interfaces:** Consumes `TradePlan, Limits, Sizing, round_trip_cost`. Produces:

```python
def size_position(plan: TradePlan, limits: Limits, profile: MarketProfile, *,
                  spread_per_side: float = 0.00075, halve: bool = False) -> Sizing
```

Algorithm (Adapted from day-trader-skill trader-budget, MIT):
1. `budget = capital * risk_per_trade_pct / 100`, halved if `halve`.
2. `R = plan.risk_per_share`; `q = floor(budget / R)`; while `q > 0 and q*R + round_trip_cost(profile, plan.mode, plan.entry, plan.stop, q, spread) > budget: q -= 1` → `qty_risk`.
3. `qty_pos = floor(capital * max_position_pct / 100 / entry)`; `qty_cash = floor(capital * (1 - cash_buffer_pct/100) / entry)`.
4. Candidates in this order `[("max loss per trade", qty_risk), ("position cap", qty_pos), ("cash after buffer", qty_cash)]`; pick the minimum (first on ties) → `qty, binding_limit`.
5. `costs = round_trip_cost(... plan.stop, qty ...)`; `risk_amount = qty*R + costs`; `notional = qty*entry`.
6. `note`: qty < 1 → `f"Stop too wide for your max loss: 1 share risks {profile.money(R + cost_1)} but your limit is {profile.money(budget)}"` (binding_limit `"max loss per trade"`); else `f"{qty} shares, limited by {binding_limit}"` plus `" (size halved after 3 stop-outs in a row)"` when halved.

- [ ] **Step 1: Failing tests** — `tests/desk/test_sizing.py`:

```python
import pytest
from nse_backtest.desk.sizing import size_position
from nse_backtest.desk.models import Limits, Mode, TradePlan
from nse_backtest.desk.markets import NSE, US
from nse_backtest.desk.costs import round_trip_cost


def plan(entry=100.0, stop=98.0, mode=Mode.SWING, market="NSE"):
    return TradePlan("X", market, mode, entry, stop, entry + 1.5 * (entry - stop), entry + 3 * (entry - stop),
                     2.0, 15, entry, "2026-10-28", "user")


def test_position_cap_binds():
    s = size_position(plan(), Limits(capital=100_000), NSE)
    assert s.qty == 250 and s.binding_limit == "position cap"
    assert s.risk_amount == pytest.approx(250 * 2 + round_trip_cost(NSE, Mode.SWING, 100, 98, 250))
    assert s.risk_amount <= 1000


def test_max_loss_binds_and_is_tight():
    s = size_position(plan(), Limits(capital=100_000, max_position_pct=100, cash_buffer_pct=0), NSE)
    q = s.qty
    assert s.binding_limit == "max loss per trade" and q > 0
    assert q * 2 + round_trip_cost(NSE, Mode.SWING, 100, 98, q) <= 1000
    assert (q + 1) * 2 + round_trip_cost(NSE, Mode.SWING, 100, 98, q + 1) > 1000


def test_stop_too_wide_refuses():
    s = size_position(plan(stop=1.0), Limits(capital=5_000), NSE)
    assert s.qty == 0 and "too wide" in s.note and s.binding_limit == "max loss per trade"


def test_halve_halves_budget():
    full = size_position(plan(), Limits(capital=100_000, max_position_pct=100, cash_buffer_pct=0), NSE)
    half = size_position(plan(), Limits(capital=100_000, max_position_pct=100, cash_buffer_pct=0), NSE, halve=True)
    assert half.size_halved and half.qty < full.qty and half.risk_amount <= 500


def test_us_market():
    s = size_position(plan(entry=50, stop=49, market="US"), Limits(capital=10_000, max_position_pct=100), US, spread_per_side=0.0003)
    assert s.qty > 0 and s.risk_amount <= 100
```

- [ ] Steps 2–5 as usual. Commit `feat(desk): position sizing to max loss with binding limit`.

---

### Task 4: Time Machine (Monte Carlo + setup history + robustness)

**Files:** Create `nse_backtest/desk/timemachine.py`, `tests/desk/test_timemachine.py`.

**Interfaces:** Consumes `TradePlan, Odds, Mode, atr_series`. Produces:

```python
def bar_ratios(df: pd.DataFrame, mode: Mode) -> np.ndarray
    # rows (O,H,L,C)/prev_close, shape (n, 4). INTRADAY: drop each session's first bar (overnight gap).
def monte_carlo(ratios, entry, stop, target, max_bars, *, cost_per_share=0.0,
                replays=10_000, seed=19, block=5) -> tuple[float, float, float, float]
    # (p_target, p_stop, p_time, expectancy_r)
def setup_history(df, plan: TradePlan, *, cost_per_share=0.0, years=3) -> list[float]
def shuffle_drawdowns(rs, *, runs=2000, seed=19) -> tuple[float, float, float, float] | None
    # (dd_p5, dd_p50, dd_p95, p_5_loss_streak) in R; None if len(rs) < 5
def noise_expectancies(df, plan, *, cost_per_share=0.0, runs=20, seed=19, jitter=0.25) -> list[float]
def odds(df, plan, *, cost_per_share=0.0, replays=10_000, seed=19, block=5,
         lookback=250, robustness=True) -> Odds
```

Algorithms (Adapted from day-trader-skill trader-timemachine, MIT; robustness ideas from Jesse):

`monte_carlo` (vectorized):
```python
n = len(ratios)
if n < 2 * block: raise ValueError("not enough history for Monte Carlo")
rng = np.random.default_rng(seed)
nblocks = -(-max_bars // block)
starts = rng.integers(0, n - block + 1, size=(replays, nblocks))
idx = (starts[:, :, None] + np.arange(block)).reshape(replays, -1)[:, :max_bars]
r = ratios[idx]                                     # (replays, max_bars, 4)
closes = entry * np.cumprod(r[:, :, 3], axis=1)
prev = np.concatenate([np.full((replays, 1), entry), closes[:, :-1]], axis=1)
o, h, l = prev * r[:, :, 0], prev * r[:, :, 1], prev * r[:, :, 2]
gap_stop, gap_tgt = o <= stop, o >= target
hit_stop, hit_tgt = l <= stop, h >= target
event = gap_stop | gap_tgt | hit_stop | hit_tgt
has = event.any(axis=1); first = event.argmax(axis=1); rows = np.arange(replays)
exit_px = closes[:, -1].copy(); outcome = np.zeros(replays, dtype=np.int8)
gs, gt, hs = gap_stop[rows, first], gap_tgt[rows, first], hit_stop[rows, first]
o_first = o[rows, first]
sel = has & gs;               exit_px[sel] = o_first[sel];  outcome[sel] = -1
sel = has & ~gs & gt;         exit_px[sel] = o_first[sel];  outcome[sel] = 1
sel = has & ~gs & ~gt & hs;   exit_px[sel] = stop;          outcome[sel] = -1   # stop wins a both-touched bar
sel = has & ~gs & ~gt & ~hs;  exit_px[sel] = target;        outcome[sel] = 1
R = entry - stop
rs = (exit_px - entry - cost_per_share) / R
return ((outcome == 1).mean(), (outcome == -1).mean(), (outcome == 0).mean(), float(rs.mean()))
```

`setup_history` (swing): compute `ema50 = Close.ewm(span=50, adjust=False).mean()`, `ema200` likewise, `atrs = atr_series(df)`, `low10 = Low.rolling(10).min()`. Iterate `i` from `max(200, len(df) - 252*years)` to `len(df) - 2`: skip unless `Close[i] > ema50[i] > ema200[i]`. `entry = Close[i]`, `stop, _ = swing_stop(entry, atrs[i], low10[i])`, `R = entry - stop`, `target = entry + plan.rr_t1 * R`. Walk bars `j = i+1 .. i+plan.max_hold_bars` (stop if past end): gap rules as above using the bar's Open; then low ≤ stop → stop; then high ≥ target → target. If the window ends past the frame end without an exit → discard the trade and stop iterating. Time exit → `Close[last j]`. `r = (exit - entry - cost_per_share * entry / plan.entry) / R`. Append; set `i = exit_j + 1` (non-overlapping).
Intraday: group 15-min bars by date, excluding the latest date. Per session: OR = first bar; find the first later bar whose Close > OR high → entry = that Close; stop = (OR high + OR low)/2; R = entry − stop (skip if ≤ 0); target = entry + plan.rr_t1·R; walk the remaining bars of the session up to `flat_by` with the same rules; time exit at the last bar's Close.

`shuffle_drawdowns`: `rng`; for each run `perm = rng.permutation(rs)`; `eq = np.cumsum(perm)`; `dd = max(np.max(np.maximum.accumulate(np.concatenate([[0], eq])) - np.concatenate([[0], eq])), 0)`; streak = any run of ≥5 consecutive `< 0`. Return `np.percentile(dds, [5, 50, 95])` and mean streak flag.

`noise_expectancies`: for each run, `rng.uniform(-jitter, jitter, len(df)) * (High - Low)` added to all of O/H/L/C of that bar (keeps OHLC consistent); `rs = setup_history(jittered, plan, ...)`; append `mean(rs)` if rs else skip.

`odds`: `ratios = bar_ratios(df.tail(lookback + 1) if SWING else df.tail(40 * 26), plan.mode)`; MC with `plan.target_1`; history; if `robustness`: shuffle + noise (negative frac = share of noise expectancies < 0); else those fields None. `history_win_pct = 100 * mean(r > 0)` and `history_expectancy_r = mean(rs)` or None when empty.

- [ ] **Step 1: Failing tests** — `tests/desk/test_timemachine.py`:

```python
import numpy as np, pandas as pd, pytest
from nse_backtest.desk.timemachine import monte_carlo, setup_history, shuffle_drawdowns, odds, bar_ratios
from nse_backtest.desk.models import Mode, TradePlan


def geometric(n=400, step=1.01, h=1.012, l=0.998):
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = 100 * step ** np.arange(1, n + 1); prev = close / step
    return pd.DataFrame({"Open": prev, "High": prev * h, "Low": prev * l, "Close": close, "Volume": 1e6}, index=idx)


def plan(entry=100.0, stop=97.0, t1=103.0, hold=15):
    return TradePlan("X", "NSE", Mode.SWING, entry, stop, t1, t1 + 3, 1.4, hold, entry, "2026-10-28", "user")


def const_ratios(o, h, l, c, n=50):
    return np.tile([o, h, l, c], (n, 1)).astype(float)


def test_uptrend_hits_target_every_path():
    pt, ps, ptime, exp = monte_carlo(const_ratios(1.0, 1.012, 0.998, 1.01), 100, 97, 103, 15)
    assert (pt, ps, ptime) == (1.0, 0.0, 0.0) and exp == pytest.approx(1.0)


def test_time_exit_when_hold_too_short():
    pt, ps, ptime, exp = monte_carlo(const_ratios(1.0, 1.012, 0.998, 1.01), 100, 97, 103, 2)
    assert ptime == 1.0 and exp == pytest.approx((100 * 1.01 ** 2 - 100) / 3)


def test_stop_wins_when_bar_touches_both():
    pt, ps, _, exp = monte_carlo(const_ratios(1.0, 1.05, 0.95, 1.0), 100, 97, 103, 15)
    assert ps == 1.0 and exp == pytest.approx(-1.0)


def test_gap_through_stop_fills_at_open():
    _, ps, _, exp = monte_carlo(const_ratios(0.9, 0.91, 0.89, 0.9), 100, 97, 103, 15)
    assert ps == 1.0 and exp == pytest.approx(-10 / 3)


def test_costs_reduce_expectancy():
    _, _, _, exp = monte_carlo(const_ratios(1.0, 1.012, 0.998, 1.01), 100, 97, 103, 15, cost_per_share=0.3)
    assert exp == pytest.approx(0.9)


def test_deterministic_under_seed():
    rng = np.random.default_rng(0)
    r = np.column_stack([np.ones(300), 1 + rng.uniform(0, .03, 300), 1 - rng.uniform(0, .03, 300), 1 + rng.normal(0, .01, 300)])
    assert monte_carlo(r, 100, 97, 104, 15, seed=7) == monte_carlo(r, 100, 97, 104, 15, seed=7)
    a = monte_carlo(r, 100, 97, 104, 15)
    assert sum(a[:3]) == pytest.approx(1.0)


def test_rejects_short_history():
    with pytest.raises(ValueError):
        monte_carlo(const_ratios(1, 1, 1, 1, n=5), 100, 97, 103, 15)


def test_setup_history_uptrend_all_wins():
    rs = setup_history(geometric(), plan(t1=104.2, stop=97.2))   # rr_t1 = 1.5
    assert len(rs) >= 20 and all(r == pytest.approx(1.5) for r in rs)


def test_setup_history_downtrend_never_fires():
    assert setup_history(geometric(step=0.99, h=1.002, l=0.988), plan()) == []


def test_shuffle_all_losses():
    assert shuffle_drawdowns([-1.0] * 6) == (6.0, 6.0, 6.0, 1.0)


def test_shuffle_all_wins_and_too_few():
    assert shuffle_drawdowns([1.0] * 10) == (0.0, 0.0, 0.0, 0.0)
    assert shuffle_drawdowns([1.0] * 4) is None


def test_odds_bundle():
    o = odds(geometric(), plan(t1=104.2, stop=97.2), replays=2000)
    assert o.replays == 2000 and o.seed == 19 and o.history_n >= 20
    assert o.history_win_pct == 100.0 and o.noise_negative_frac == 0.0
    assert o.dd_p95_r == 0.0 and len(o.history_rs) == o.history_n


def test_odds_without_robustness():
    o = odds(geometric(), plan(t1=104.2, stop=97.2), replays=500, robustness=False)
    assert o.dd_p50_r is None and o.noise_negative_frac is None


def test_intraday_ratios_drop_session_first_bar():
    idx = pd.date_range("2026-10-27 09:15", periods=4, freq="15min").append(pd.date_range("2026-10-28 09:15", periods=4, freq="15min"))
    df = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0}, index=idx)
    assert bar_ratios(df, Mode.INTRADAY).shape == (6, 4)
```

- [ ] Steps 2–5. Also time the full test file: must finish < 30 s (`pytest --durations=5`). Commit `feat(desk): time machine — monte carlo, setup history, shuffle and noise robustness`.

---

### Task 5: NSE ban list + checks

**Files:** Create `nse_backtest/desk/nse_lists.py`, `nse_backtest/desk/checks.py`, `tests/desk/test_nse_lists.py`, `tests/desk/test_checks.py`.

**Interfaces — Produces:**

```python
# nse_lists.py
FNO_BAN_URL = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
@dataclass(frozen=True)
class BanList:
    symbols: frozenset[str]; status: str      # "ok" | "cached" | "stale" | "unavailable"
    as_of: str | None; detail: str
def parse_fno_ban(text: str) -> frozenset[str]
    # requires "ban" (case-insensitive) in the first non-empty line else ValueError("unexpected ban list format");
    # symbols from lines matching r"^\s*\d+\s*,\s*([A-Z0-9&\-]+)\s*$"
def fetch_fno_ban(cache_dir: Path, today: date, fetcher: Callable[[str], str] | None = None) -> BanList
    # fetcher default: urllib GET with User-Agent "Mozilla/5.0", timeout 10s, utf-8 decode.
    # success -> write cache_dir/"fo_secban.csv" + cache_dir/"fo_secban.date" (today ISO), status "ok".
    # failure (any Exception incl. parse) -> cached file: status "cached" if its date == today else "stale";
    # no cache -> BanList(frozenset(), "unavailable", None, str(err)). Never raises.

# checks.py
@dataclass(frozen=True)
class CheckContext:
    profile: MarketProfile; limits: Limits; now: datetime       # tz-aware, in profile.tz
    regime: str | None = None                                   # TRENDING / MIXED / HOSTILE
    journal: tuple[dict, ...] = ()
    governor_can_trade: bool | None = None; governor_reasons: tuple[str, ...] = ()
    ban: BanList | None = None
    next_earnings: date | None = None; earnings_checked: bool = False
    turnover_20d: float | None = None; today_gap_pct: float | None = None
    day_trades_5d: int = 0
def stoploss_streak(journal, market: str, n: int = 3) -> bool
    # last n closed trades of that market (by closed_date/exit_date/date; market missing -> "NSE") all pnl < 0
def realised_today(journal, market: str, today: date) -> float
def run_checks(plan: TradePlan, sizing: Sizing, odds: Odds | None, ctx: CheckContext) -> list[Check]
```

Check names and rules — implement exactly, in this order (each returns one `Check`):
1. `regime`: SWING: HOSTILE→BLOCK "Market regime HOSTILE: swing longs historically lose here (held-out 2026: −1.61%/trade)"; MIXED→WARN; TRENDING→PASS; None→WARN "Regime unknown". INTRADAY: HOSTILE→WARN, else PASS.
2. `reward_risk`: `plan.rr_t1 < limits.min_rr` → BLOCK; else PASS (reason shows `1:{rr:.2f}`).
3. `size`: `sizing.qty < 1` → BLOCK with `sizing.note`; else PASS.
4. `risk_governor`: None→WARN "not evaluated"; False→BLOCK "; ".join(reasons); True→PASS.
5. `daily_loss`: `-realised_today(...) >= capital*daily_loss_limit_pct/100` → BLOCK; else PASS.
6. `ban_list`: `not profile.has_ban_list` → PASS "No ban list for {code}"; ban None or status "unavailable" → WARN "Could not verify F&O ban list"; symbol in ban.symbols → BLOCK; status "stale" → WARN; else PASS.
7. `monte_carlo`: odds None → WARN "odds unavailable"; `mc_expectancy_r < 0` → BLOCK; `< 0.1` → WARN "thin"; else PASS.
8. `freshness`: last_bar parsed; SWING: `(now.date() - last_bar.date()).days > 3` → BLOCK; INTRADAY: localize naive last_bar to profile.tz; `now - last_bar > 30 min` → BLOCK; else PASS.
9. `entry_window`: SWING → PASS; INTRADAY: weekend or `now.time()` outside `[no_entry_before, no_entry_after]` → BLOCK; else PASS.
10. `liquidity`: None → WARN; `< profile.min_turnover` → BLOCK; else PASS.
11. `earnings`: not checked → WARN "Could not check earnings date"; SWING and `0 <= (next - now.date()).days <= ceil(max_hold*7/5)` → WARN; INTRADAY and next == today → WARN; else PASS.
12. `setup_history`: odds None → WARN; INTRADAY: `n < 20 or exp is None or exp < 0.2` → BLOCK "Research only: this setup hasn't earned +0.2R over ≥20 past sessions on this stock"; SWING: `n < 20` → WARN "too few samples"; `exp < 0` → WARN; else PASS.
13. `stoploss_streak`: streak → WARN "3 stop-outs in a row — size halved"; else PASS.
14. `gap`: `abs(today_gap_pct) > 2` → WARN; None or ≤2 → PASS.
15. `robustness`: `noise_negative_frac` None → PASS "not run"; `> 0.25` → WARN "edge is fragile"; else PASS.
16. `pdt`: US & INTRADAY & `limits.pdt_guard` & `day_trades_5d >= 3` → BLOCK; else PASS.

- [ ] **Step 1: Failing tests.** `tests/desk/test_nse_lists.py`:

```python
from datetime import date
import pytest
from nse_backtest.desk.nse_lists import parse_fno_ban, fetch_fno_ban

SAMPLE = "Securities in Ban For Trade Date 29-OCT-2026:\n1,ABCLTD\n2,XYZ-RE\n"


def test_parse():
    assert parse_fno_ban(SAMPLE) == frozenset({"ABCLTD", "XYZ-RE"})
    assert parse_fno_ban("Securities in Ban For Trade Date 29-OCT-2026: NIL\n") == frozenset()


def test_parse_rejects_unknown_format():
    with pytest.raises(ValueError):
        parse_fno_ban("<html>blocked</html>")


def test_fetch_ok_then_cached_then_stale(tmp_path):
    ok = fetch_fno_ban(tmp_path, date(2026, 10, 29), fetcher=lambda u: SAMPLE)
    assert ok.status == "ok" and "ABCLTD" in ok.symbols

    def boom(u): raise OSError("down")
    assert fetch_fno_ban(tmp_path, date(2026, 10, 29), fetcher=boom).status == "cached"
    stale = fetch_fno_ban(tmp_path, date(2026, 10, 30), fetcher=boom)
    assert stale.status == "stale" and "ABCLTD" in stale.symbols


def test_fetch_unavailable_never_raises(tmp_path):
    b = fetch_fno_ban(tmp_path, date(2026, 10, 29), fetcher=lambda u: "<html/>")
    assert b.status == "unavailable" and b.symbols == frozenset()
```

`tests/desk/test_checks.py` — build a passing baseline then flip one input per test:

```python
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo
import pytest
from nse_backtest.desk.checks import CheckContext, run_checks, stoploss_streak
from nse_backtest.desk.models import Limits, Mode, Odds, Sizing, Status, TradePlan
from nse_backtest.desk.markets import NSE, US
from nse_backtest.desk.nse_lists import BanList

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 29, 10, 0, tzinfo=IST)


def plan(mode=Mode.SWING, last_bar="2026-10-28T00:00:00", t1=103.0, market="NSE"):
    return TradePlan("ABC", market, mode, 100, 98, t1, 106, 2, 15, 100, last_bar, "user")


SIZE = Sizing(100, "position cap", 250.0, 10_000.0, 50.0, False, "100 shares")
ODDS = Odds(10_000, 19, .5, .3, .2, .4, 30, 55.0, .3, (0.3,) * 30, 1, 3, 6, .1, .2, .5, 0.0)


def ctx(**kw):
    base = dict(profile=NSE, limits=Limits(capital=100_000), now=NOW, regime="TRENDING",
                governor_can_trade=True, ban=BanList(frozenset(), "ok", "2026-10-29", ""),
                next_earnings=date(2026, 12, 30), earnings_checked=True, turnover_20d=1e9, today_gap_pct=0.5)
    base.update(kw)
    return CheckContext(**base)


def by_name(checks):
    return {c.name: c.status for c in checks}


def test_baseline_all_pass_with_sixteen_checks():
    cs = run_checks(plan(), SIZE, ODDS, ctx())
    assert len(cs) == 16 and all(c.status is Status.PASS for c in cs), [c for c in cs if c.status is not Status.PASS]


@pytest.mark.parametrize("override,name,status", [
    (dict(regime="HOSTILE"), "regime", Status.BLOCK),
    (dict(regime="MIXED"), "regime", Status.WARN),
    (dict(regime=None), "regime", Status.WARN),
    (dict(governor_can_trade=False, governor_reasons=("max positions",)), "risk_governor", Status.BLOCK),
    (dict(governor_can_trade=None), "risk_governor", Status.WARN),
    (dict(ban=BanList(frozenset({"ABC"}), "ok", None, "")), "ban_list", Status.BLOCK),
    (dict(ban=None), "ban_list", Status.WARN),
    (dict(ban=BanList(frozenset(), "stale", "2026-10-20", "")), "ban_list", Status.WARN),
    (dict(turnover_20d=1e6), "liquidity", Status.BLOCK),
    (dict(turnover_20d=None), "liquidity", Status.WARN),
    (dict(earnings_checked=False), "earnings", Status.WARN),
    (dict(next_earnings=date(2026, 11, 5)), "earnings", Status.WARN),
    (dict(today_gap_pct=-3.1), "gap", Status.WARN),
    (dict(journal=tuple({"pnl": -1500, "closed_date": "2026-10-29"} for _ in range(2))), "daily_loss", Status.BLOCK),
])
def test_single_check_flips(override, name, status):
    assert by_name(run_checks(plan(), SIZE, ODDS, ctx(**override)))[name] is status


def test_reward_risk_and_size_and_odds_blocks():
    assert by_name(run_checks(plan(t1=101), SIZE, ODDS, ctx()))["reward_risk"] is Status.BLOCK
    assert by_name(run_checks(plan(), replace(SIZE, qty=0, note="too wide"), ODDS, ctx()))["size"] is Status.BLOCK
    assert by_name(run_checks(plan(), SIZE, replace(ODDS, mc_expectancy_r=-0.1), ctx()))["monte_carlo"] is Status.BLOCK
    assert by_name(run_checks(plan(), SIZE, replace(ODDS, noise_negative_frac=0.5), ctx()))["robustness"] is Status.WARN
    assert by_name(run_checks(plan(), SIZE, None, ctx()))["monte_carlo"] is Status.WARN


def test_stale_swing_data_blocks():
    assert by_name(run_checks(plan(last_bar="2026-10-20T00:00:00"), SIZE, ODDS, ctx()))["freshness"] is Status.BLOCK


def test_intraday_window_freshness_and_history_gate():
    p = plan(mode=Mode.INTRADAY, last_bar="2026-10-29T09:45:00")
    assert by_name(run_checks(p, SIZE, ODDS, ctx()))["entry_window"] is Status.PASS
    late = ctx(now=datetime(2026, 10, 29, 14, 45, tzinfo=IST))
    r = by_name(run_checks(p, SIZE, ODDS, late))
    assert r["entry_window"] is Status.BLOCK and r["freshness"] is Status.BLOCK
    assert by_name(run_checks(p, SIZE, replace(ODDS, history_expectancy_r=0.1), ctx()))["setup_history"] is Status.BLOCK


def test_us_no_ban_list_and_pdt():
    et = datetime(2026, 10, 29, 10, 30, tzinfo=ZoneInfo("America/New_York"))
    p = plan(mode=Mode.INTRADAY, last_bar="2026-10-29T10:15:00", market="US")
    c = ctx(profile=US, now=et, ban=None, limits=Limits(capital=10_000, pdt_guard=True), day_trades_5d=3, turnover_20d=1e9)
    r = by_name(run_checks(p, SIZE, ODDS, c))
    assert r["ban_list"] is Status.PASS and r["pdt"] is Status.BLOCK


def test_stoploss_streak_per_market():
    j = [{"pnl": -1, "closed_date": f"2026-10-2{i}"} for i in range(3)]
    assert stoploss_streak(j, "NSE") and not stoploss_streak(j, "US")
    assert not stoploss_streak(j + [{"pnl": 5, "closed_date": "2026-10-28"}], "NSE")
```

- [ ] Steps 2–5. Commit `feat(desk): F&O ban list and 16 PASS/WARN/BLOCK checks`.

---

### Task 6: Verdict, audit, check_trade service

**Files:** Create `nse_backtest/desk/{verdict,audit,service}.py`, `tests/desk/test_verdict_service.py`.

**Interfaces — Produces:**

```python
# verdict.py
def ticket_lines(plan, sizing, profile) -> tuple[str, ...]
    # NSE SWING:    (f"BUY {qty} {SYM} CNC LIMIT {entry:.2f}", f"GTT OCO {SYM}: stop-loss {stop:.2f} / target {t1:.2f}")
    # NSE INTRADAY: (f"BUY {qty} {SYM} MIS LIMIT {entry:.2f}", f"SL-M SELL {qty} {SYM} trigger {stop:.2f}",
    #                f"SELL {qty} {SYM} LIMIT {t1:.2f}", f"Square off by {flat_by:%H:%M} IST")
    # US:           (f"BUY {qty} {SYM} LIMIT {entry:.2f} — bracket: stop-loss {stop:.2f}, take-profit {t1:.2f}, {'DAY' if intraday else 'GTC'}",)
    #               + intraday: (f"Flat by {flat_by:%H:%M} ET",)
def input_hash(plan, sizing, odds, checks, current_price) -> str   # sha256 hex of json.dumps(asdict(...), sort_keys=True, default=str)
def decide(plan, sizing, odds, checks, profile, current_price, now: datetime) -> DeskVerdict
    # any BLOCK -> SKIP, ticket (), trigger None,
    #   headline f"SKIP {SYM} — blocked by: {', '.join(names)}"
    # current_price < entry*(1-0.001) -> WAIT, trigger=entry, ticket = ("IF TRIGGERED:",)+lines,
    #   headline f"WAIT {SYM} — buy only above {money(entry)} (now {money(current_price)})"
    # current_price > entry*1.01 -> WAIT, trigger=entry, headline f"WAIT {SYM} — price ran past entry; buy only on a pullback to {money(entry)}"
    # else TAKE: headline f"TAKE {SYM} — BUY {qty} @ {money(entry)}, max loss {money(risk_amount)}"
    # created_at = now.isoformat()

# audit.py
def data_dir() -> Path                        # $NSE_LAB_DATA_DIR or ~/.nse-trading-lab (mkdir parents, exist_ok)
def append_verdict(v: DeskVerdict, path: Path | None = None) -> Path
    # one JSON line {created_at, action, symbol, market, mode, entry, stop, target_1, qty, risk_amount,
    #  mc_expectancy_r, blocks:[names], warns:[names], input_hash}; file chmod 0o600 on create

# service.py
def check_trade(df, symbol, profile, mode, limits, ctx: CheckContext, *, entry=None, stop=None,
                target=None, robustness=True, replays=10_000, audit=True, audit_path=None) -> DeskVerdict
```

`check_trade` steps: `plan = build_plan(...)`; `halve = stoploss_streak(ctx.journal, profile.code)`; `spread = spread_pct_for_df(df) if SWING else 0.00075`; `sizing = size_position(plan, limits, profile, spread_per_side=spread, halve=halve)`; `q = max(sizing.qty, 1)`; `cps = round_trip_cost(profile, mode, plan.entry, plan.target_1, q, spread) / q`; `odds = timemachine.odds(df, plan, cost_per_share=cps, replays=replays, robustness=robustness)` — on `ValueError` → `odds=None`; `checks = run_checks(...)`; `v = decide(plan, sizing, odds, checks, profile, plan.last_close, ctx.now)`; `append_verdict` if audit; return `v`. Note: the user's `entry` may differ from last close, which is what makes WAIT possible.

- [ ] **Step 1: Failing tests** — `tests/desk/test_verdict_service.py`:

```python
import json
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd, pytest
from nse_backtest.desk.verdict import decide, ticket_lines
from nse_backtest.desk.service import check_trade
from nse_backtest.desk.checks import CheckContext
from nse_backtest.desk.models import Action, Check, Limits, Mode, Sizing, Status, TradePlan
from nse_backtest.desk.markets import NSE, US
from nse_backtest.desk.nse_lists import BanList

NOW = datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
P = TradePlan("ABC", "NSE", Mode.SWING, 100, 98, 103, 106, 2, 15, 100, "2026-10-28T00:00:00", "user")
S = Sizing(250, "position cap", 640.0, 25_000.0, 140.0, False, "250 shares")
OK = (Check("regime", Status.PASS, ""),)


def test_take_ticket_nse_swing():
    v = decide(P, S, None, OK, NSE, 100.0, NOW)
    assert v.action is Action.TAKE and v.ticket == ("BUY 250 ABC CNC LIMIT 100.00", "GTT OCO ABC: stop-loss 98.00 / target 103.00")
    assert v.headline == "TAKE ABC — BUY 250 @ ₹100.00, max loss ₹640.00" and len(v.input_hash) == 64


def test_block_means_skip():
    v = decide(P, S, None, OK + (Check("regime", Status.BLOCK, "x"), Check("gap", Status.WARN, "y")), NSE, 100.0, NOW)
    assert v.action is Action.SKIP and v.ticket == () and v.headline == "SKIP ABC — blocked by: regime"


def test_wait_below_trigger_and_after_runaway():
    below = decide(P, S, None, OK, NSE, 99.0, NOW)
    assert below.action is Action.WAIT and below.trigger == 100 and below.ticket[0] == "IF TRIGGERED:"
    assert decide(P, S, None, OK, NSE, 101.5, NOW).action is Action.WAIT


def test_us_and_intraday_tickets():
    us = ticket_lines(replace(P, market="US"), S, US)
    assert us == ("BUY 250 ABC LIMIT 100.00 — bracket: stop-loss 98.00, take-profit 103.00, GTC",)
    mis = ticket_lines(replace(P, mode=Mode.INTRADAY), S, NSE)
    assert mis[0].startswith("BUY 250 ABC MIS LIMIT") and mis[-1] == "Square off by 15:15 IST"


def test_hash_is_stable_and_input_sensitive():
    a = decide(P, S, None, OK, NSE, 100.0, NOW).input_hash
    assert a == decide(P, S, None, OK, NSE, 100.0, NOW).input_hash
    assert a != decide(replace(P, stop=97), S, None, OK, NSE, 100.0, NOW).input_hash


def rising(n=400):
    idx = pd.bdate_range(end="2026-10-28", periods=n)
    close = 100 * 1.004 ** np.arange(1, n + 1); prev = close / 1.004
    return pd.DataFrame({"Open": prev, "High": prev * 1.012, "Low": prev * 0.995, "Close": close, "Volume": 5e6}, index=idx)


def test_check_trade_end_to_end_writes_audit(tmp_path):
    c = CheckContext(profile=NSE, limits=Limits(capital=100_000), now=NOW, regime="TRENDING", governor_can_trade=True,
                     ban=BanList(frozenset(), "ok", None, ""), earnings_checked=True, turnover_20d=1e10, today_gap_pct=0.1)
    path = tmp_path / "audit.jsonl"
    v = check_trade(rising(), "ABC", NSE, Mode.SWING, Limits(capital=100_000), c, replays=1000, audit_path=path)
    assert v.action in (Action.TAKE, Action.WAIT, Action.SKIP) and v.odds is not None
    assert len(v.checks) == 16
    line = json.loads(path.read_text().strip().splitlines()[-1])
    assert line["symbol"] == "ABC" and line["input_hash"] == v.input_hash


def test_check_trade_hostile_is_skip(tmp_path):
    c = CheckContext(profile=NSE, limits=Limits(capital=100_000), now=NOW, regime="HOSTILE")
    v = check_trade(rising(), "ABC", NSE, Mode.SWING, Limits(capital=100_000), c, replays=500, robustness=False, audit_path=tmp_path / "a.jsonl")
    assert v.action is Action.SKIP and "regime" in v.headline
```

- [ ] Steps 2–5. Commit `feat(desk): verdict, tickets, audit log and check_trade service`.

---

### Task 7: Positions, daily plan, intraday ORB scan

**Files:** Create `nse_backtest/desk/{positions,daily,intraday}.py`, `tests/desk/test_daily_positions_intraday.py`.

**Interfaces — Produces:**

```python
# positions.py
@dataclass(frozen=True)
class PositionAction:
    symbol: str; action: str      # "EXIT" | "TIGHTEN_STOP" | "HOLD" | "DATA UNAVAILABLE"
    current_price: float | None; entry: float; stop: float; suggested_stop: float | None
    r_so_far: float | None; pnl_pct: float | None; reason: str
def review_positions(positions: list[dict], frames: dict[str, pd.DataFrame], profile) -> list[PositionAction]
    # only open positions (not p.get("closed")) whose p.get("market", "NSE") == profile.code;
    # missing/empty frame -> DATA UNAVAILABLE; else position_monitor.daily_check(p, df);
    # r_so_far = (cur - entry) / (entry - p.get("initial_stop", p["stop_loss"])) when denominator > 0;
    # order: DATA UNAVAILABLE, EXIT, TIGHTEN_STOP, HOLD

# daily.py
@dataclass(frozen=True)
class DailyPlan:
    market: str; regime: str | None; regime_text: str; flip_conditions: tuple[str, ...]
    take: tuple[DeskVerdict, ...]; watch: tuple[DeskVerdict, ...]
    skipped: tuple[tuple[str, str], ...]; unavailable: tuple[str, ...]
def flip_conditions(regime_df: pd.DataFrame | None, profile) -> tuple[str, ...]
    # computes close, ema50, ema200 (ewm adjust=False), ret_60d %, slope_200 % over 20 bars, same as tape_monitor;
    # lists every TRENDING condition NOT met, each with numbers, e.g.
    # f"{profile.regime_index} close above 50-EMA (now {close:,.0f} vs {ema50:,.0f})",
    # "50-EMA above 200-EMA (golden cross)", f"60-day return above +5% (now {ret:+.1f}%)",
    # f"200-EMA rising > +0.5% per 20 days (now {slope:+.2f}%)"; empty tuple when TRENDING; ("Regime index data unavailable",) when None/short
def breakout_entry(df) -> float | None
    # trigger = df.High.tail(20).max(); close >= trigger*0.999 -> None (already at/through, plan at close);
    # close < trigger/1.05 -> float("nan") meaning "not near a setup"; else trigger*1.001
def daily_plan(profile, frames: dict[str, pd.DataFrame], regime_df, limits, ctx_base: CheckContext,
               *, top_take=3, top_watch=5, replays=3000) -> DailyPlan
    # regime from tape_monitor.assess_tape(regime_df); ctx = replace(ctx_base, regime=...);
    # per symbol: frame missing/len<220 -> unavailable; e = breakout_entry(df); nan -> skipped (sym, "not near a setup");
    # v = check_trade(df, sym, profile, SWING, limits, replace(ctx, turnover_20d=..., today_gap_pct=...), entry=e,
    #                 robustness=False, replays=replays, audit=False); ValueError -> skipped (sym, str(err))
    # take = TAKE sorted by mc_expectancy_r desc [:top_take], each re-run with robustness=True and audit=True
    #        (keep only those still TAKE; demoted ones go to watch);
    # watch = WAIT plus SKIPs whose only BLOCKs are {"regime"}, sorted by (trigger or entry)/current_price asc [:top_watch]
    # everything else -> skipped (sym, headline)
    # regime_text = TapeAssessment.recommendation or "Regime unknown — treat as HOSTILE"

# intraday.py
def orb_scan(profile, frames15: dict[str, pd.DataFrame], limits, ctx_base, *, replays=3000
             ) -> tuple[list[DeskVerdict], list[tuple[str, str]]]
    # per symbol: check_trade(df, sym, profile, INTRADAY, limits, ctx, robustness=False, replays=replays, audit=False);
    # any ValueError (empty/short frame, bad geometry) -> (sym, str(err)) in failures — never silently dropped.
    # verdicts sorted TAKE, WAIT, SKIP, then mc_expectancy_r desc (None last).
```

Helpers to add in `nse_backtest/desk/loaders.py` are NOT needed here; compute `turnover_20d = float((df.Close*df.Volume).tail(20).median())` and `today_gap_pct = (df.Open.iloc[-1]/df.Close.iloc[-2]-1)*100` in a small pure helper `market_stats(df) -> tuple[float, float]` inside `daily.py` (reused by the service callers).

- [ ] **Step 1: Failing tests** — `tests/desk/test_daily_positions_intraday.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd, pytest
from nse_backtest.desk.positions import review_positions
from nse_backtest.desk.daily import daily_plan, flip_conditions, breakout_entry
from nse_backtest.desk.intraday import orb_scan
from nse_backtest.desk.checks import CheckContext
from nse_backtest.desk.models import Action, Limits
from nse_backtest.desk.markets import NSE
from nse_backtest.desk.nse_lists import BanList

NOW = datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def series(n=400, step=1.004, end="2026-10-28"):
    idx = pd.bdate_range(end=end, periods=n)
    close = 100 * step ** np.arange(1, n + 1); prev = close / step
    return pd.DataFrame({"Open": prev, "High": prev * 1.012, "Low": prev * 0.995, "Close": close, "Volume": 5e6}, index=idx)


def ctx():
    return CheckContext(profile=NSE, limits=Limits(capital=100_000), now=NOW, governor_can_trade=True,
                        ban=BanList(frozenset(), "ok", None, ""), earnings_checked=True)


def test_review_positions_orders_and_filters():
    pos = [{"symbol": "AAA", "buy_price": 100, "qty": 10, "stop_loss": 95, "target": 120, "entry_date": "2026-09-01"},
           {"symbol": "MISSING", "buy_price": 50, "qty": 1, "stop_loss": 45, "target": 60},
           {"symbol": "USX", "market": "US", "buy_price": 10, "qty": 1, "stop_loss": 9, "target": 12},
           {"symbol": "OLD", "closed": True, "buy_price": 1, "qty": 1, "stop_loss": 0.5, "target": 2}]
    out = review_positions(pos, {"AAA": series()}, NSE)
    assert [o.symbol for o in out] == ["MISSING", "AAA"]
    assert out[0].action == "DATA UNAVAILABLE" and out[1].r_so_far is not None


def test_flip_conditions():
    assert flip_conditions(series(step=1.004), NSE) == ()
    falling = flip_conditions(series(step=0.997), NSE)
    assert any("50-EMA" in s for s in falling) and any("60-day" in s for s in falling)
    assert flip_conditions(None, NSE) == ("Regime index data unavailable",)


def test_breakout_entry_modes():
    df = series()
    assert breakout_entry(df) is None                       # making new highs -> plan at close
    df2 = df.copy(); df2.iloc[-1, df2.columns.get_loc("Close")] = df2.High.tail(20).max() / 1.03
    assert breakout_entry(df2) == pytest.approx(df2.High.tail(20).max() * 1.001)
    df3 = df.copy(); df3.iloc[-1, df3.columns.get_loc("Close")] = df3.High.tail(20).max() / 1.2
    assert np.isnan(breakout_entry(df3))


def test_daily_plan_hostile_puts_ready_setups_on_watch():
    frames = {"AAA": series(), "BBB": series(), "SHORT": series(n=50)}
    dp = daily_plan(NSE, frames, series(step=0.997), Limits(capital=100_000), ctx(), replays=500)
    assert dp.regime == "HOSTILE" and dp.take == ()
    assert {v.plan.symbol for v in dp.watch} == {"AAA", "BBB"} and dp.unavailable == ("SHORT",)
    assert dp.flip_conditions


def test_orb_scan_returns_failures_not_silence():
    bad = pd.DataFrame({"Open": [], "High": [], "Low": [], "Close": []})
    verdicts, failures = orb_scan(NSE, {"EMPTY": bad}, Limits(capital=100_000), ctx(), replays=200)
    assert verdicts == [] and failures and failures[0][0] == "EMPTY"
```

- [ ] Steps 2–5. Commit `feat(desk): daily plan, position review and intraday ORB scan`.

---

### Task 8: Loaders, settings, CLI, Claude skill, Kite MCP

**Files:** Create `nse_backtest/desk/{loaders,cli,__main__}.py`, `tests/desk/test_cli_loaders.py`, `.claude/skills/trade-desk/SKILL.md`, `.mcp.json`. Read `components/state.py` first to match the positions/journal JSON shapes and file names.

**Interfaces — Produces:**

```python
# loaders.py  (the ONLY module besides nse_lists that touches the network or user files)
def load_daily(profile, symbol, years=4) -> pd.DataFrame          # fetch_nse(sym, start, exchange=profile.exchange)
def load_intraday(profile, symbol) -> pd.DataFrame                # yfinance download period="60d", interval="15m",
                                                                  # flatten columns, index tz-convert to profile.tz then tz-naive
def load_regime(profile) -> pd.DataFrame | None                   # daily bars of profile.regime_index; None on failure (logged)
def next_earnings(profile, symbol) -> tuple[date | None, bool]    # yfinance Ticker(profile.yf_symbol(sym)).calendar; (None, False) on failure
def load_positions() -> list[dict]; def load_journal() -> list[dict]   # data_dir()/positions.json, trade_journal.json; [] if absent
def load_limits(profile) -> Limits; def save_limits(profile, limits) -> None
    # data_dir()/desk_settings.json {"NSE": {...}, "US": {...}}; defaults Limits(capital=100_000) NSE, Limits(capital=10_000) US
def build_context(profile, limits, *, now=None, symbol_df=None) -> CheckContext
    # now default datetime.now(ZoneInfo(profile.tz)); regime filled by caller; governor via
    # components.risk_governor.assess(market_positions, market_journal, limits.capital, regime);
    # ban via nse_lists.fetch_fno_ban(data_dir()/"cache", now.date()) for NSE else None
```

CLI (`python -m nse_backtest.desk`):
```
check SYMBOL [--market NSE|US] [--mode swing|intraday] [--entry X] [--stop X] [--target X] [--json]
plan [--market NSE|US] [--json]
positions [--market NSE|US] [--json]
intraday [--market NSE|US] [--symbols A,B,...] [--json]
settings [--market NSE|US] [--capital X] [--risk-pct X] [--max-position-pct X] [--daily-loss-pct X] [--pdt-guard on|off]
```
Text output for `check`: headline, then plan (entry/stop/T1/T2/R, basis), sizing note, odds (P target/stop/time, MC expectancy R, history n/win%/exp R, worst DD p95, P(5 losses in a row), noise range), each check as `PASS|WARN|BLOCK name — reason`, ticket lines, and the footer `Not financial advice. Nothing was placed. Paper-trade until your record matches the odds.` `--json` prints `dataclasses.asdict` (enums as values). Exit 2 with `error: ...` on ValueError.

- [ ] **Step 1: Failing tests** — `tests/desk/test_cli_loaders.py` (all network monkeypatched):

```python
import json
import numpy as np, pandas as pd, pytest
from nse_backtest.desk import cli, loaders
from nse_backtest.desk.markets import NSE, US
from nse_backtest.desk.models import Limits


def series(n=400):
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=n)
    close = 100 * 1.004 ** np.arange(1, n + 1); prev = close / 1.004
    return pd.DataFrame({"Open": prev, "High": prev * 1.012, "Low": prev * 0.995, "Close": close, "Volume": 5e6}, index=idx)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: series())
    monkeypatch.setattr(loaders, "load_regime", lambda p: series())
    monkeypatch.setattr(loaders, "next_earnings", lambda p, s: (None, True))
    monkeypatch.setattr(loaders.nse_lists, "fetch_fno_ban",
                        lambda d, t, fetcher=None: loaders.nse_lists.BanList(frozenset(), "ok", None, ""))


def test_limits_roundtrip_per_market(tmp_path):
    loaders.save_limits(NSE, Limits(capital=123_456))
    assert loaders.load_limits(NSE).capital == 123_456 and loaders.load_limits(US).capital == 10_000


def test_cli_check_text(capsys):
    assert cli.main(["check", "ABC", "--market", "NSE"]) == 0
    out = capsys.readouterr().out
    assert any(out.startswith(w) for w in ("TAKE", "WAIT", "SKIP")) and "Nothing was placed" in out
    assert "PASS" in out or "WARN" in out


def test_cli_check_json(capsys):
    assert cli.main(["check", "ABC", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["plan"]["symbol"] == "ABC" and d["action"] in ("TAKE", "WAIT", "SKIP")


def test_cli_bad_stop_exits_2(capsys):
    assert cli.main(["check", "ABC", "--entry", "100", "--stop", "105"]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_settings(capsys):
    assert cli.main(["settings", "--market", "US", "--capital", "2500", "--pdt-guard", "on"]) == 0
    l = loaders.load_limits(US)
    assert l.capital == 2500 and l.pdt_guard is True
```

- [ ] **Step 3 also:** write `.claude/skills/trade-desk/SKILL.md`:

```markdown
---
name: trade-desk
description: >-
  NSE + US trade desk for this repo. Turns a stock into a sized TAKE / WAIT / SKIP decision with
  stop, targets, Monte Carlo odds, setup history and 16 risk checks; also the daily plan, position
  review and intraday opening-range scan. Use when the user says "check RELIANCE", "should I buy",
  "size this trade", "what should I trade today", "review my positions", "any intraday setups",
  "set my capital / max loss". Never places orders.
argument-hint: "[check SYMBOL | plan | positions | intraday | settings] [--market NSE|US]"
---

# trade-desk

Run from the repo root with `python3.13 -m nse_backtest.desk <command>`:

    check RELIANCE --market NSE                     # default swing plan at last close
    check AAPL --market US --entry 231 --stop 226   # the user's own plan
    check INFY --mode intraday                      # opening-range breakout
    plan --market NSE                               # today's plan: regime, TAKE, WATCH
    positions --market US
    intraday --market NSE
    settings --market NSE --capital 750000 --risk-pct 1

Rules for Claude:
- Report the verdict line first, then every BLOCK and WARN in plain words, then the ticket.
- Never soften a SKIP or a negative expectancy. "No trade" is a valid answer; say what would change it.
- Never place, modify or cancel an order, and never call a broker order tool. The user types the
  ticket into Kite or their US broker themselves.
- Lead with expectancy in R, not win rate.
- If the user asks for portfolio context and the `kite` MCP server is connected, use its read-only
  holdings/positions tools only.
- Not financial advice; the odds are a reality check from the stock's own history, not a forecast.
```

and `.mcp.json`:

```json
{
  "mcpServers": {
    "kite": { "type": "http", "url": "https://mcp.kite.trade/mcp" }
  }
}
```

(The hosted Kite MCP exposes no order-placement tools; login happens in the browser via Kite, so no key is ever pasted.)

- [ ] **Step 3b — vendor US research skills** (security audit 2026-10-29: SAFE-WITH-NOTES). Copy
  `trader-news/` and `trader-analyst/` from day-trader-skill (commit pinned in `.claude/skills/VENDORED.md`)
  into `.claude/skills/`, plus its `LICENSE` as `.claude/skills/LICENSE-day-trader-skill`. In the vendored
  `news.py`, validate every ticker against `^[A-Z][A-Z0-9.\-]{0,9}$` before it reaches a URL (audit N2) and
  `urllib.parse.quote` it. Write `.claude/skills/VENDORED.md` recording source, commit, audit verdict, the
  N1–N6 notes, and the rule: these skills are research context in Claude sessions only — never inputs to a
  desk verdict. Do not vendor `trader-broker` (audit N4/N5).
- [ ] Steps 2, 4, 5. Commit `feat(desk): loaders, per-market settings, CLI, trade-desk skill, read-only Kite MCP, vendored US research skills`.

---

### Task 9: Dashboard page + smoke tests

**Files:** Create `pages/0_Desk.py`, `tests/test_desk_page_smoke.py`. Modify `tests/test_pages_interactive_smoke.py:68` (stale `pages/2_Dashboard.py` → `pages/0_Desk.py`, adapt assertions to the new page or delete that test function if it asserted Dashboard-specific widgets). Read `pages/0_Today.py` and `components/theme.py` first and follow their page-setup conventions; check how `app.py` builds navigation and register `0_Desk.py` there as the default page if `app.py` uses `st.navigation`; otherwise the file-name prefix orders it.

Page layout (Streamlit):
- Sidebar: market radio `NSE` / `US`; settings expander (capital, risk %, max position %, daily loss %, PDT guard for US) bound to `loaders.load_limits/save_limits`; a "Paper mode — nothing is ever placed" caption.
- Header: regime badge (color from `TapeAssessment.color`) + regime_text + flip conditions.
- Tabs: `Today's Plan`, `Check a Trade`, `My Positions`, `Intraday`.
  - Today's Plan: button "Build today's plan" (results cached in `st.session_state` keyed by market+date) → TAKE cards, WATCH table (symbol, trigger, distance %, what's blocking), collapsed "skipped / unavailable" list.
  - Check a Trade: form (symbol, mode, optional entry/stop/target) → verdict card: big action word + headline; plan table; sizing note; odds metrics; checks table with PASS/WARN/BLOCK coloured; ticket in `st.code`; a histogram of `history_rs` (plotly) when n > 0; footer disclaimer.
  - My Positions: `review_positions` table, EXIT/TIGHTEN rows highlighted.
  - Intraday: symbols multiselect (default first 15 of universe) + "Scan" → verdict table; non-TAKE rows greyed with reason.
- Every loader call wrapped: failure shows `st.error` with the reason and continues (never a blank tab).

- [ ] **Step 1: Failing smoke test** — `tests/test_desk_page_smoke.py`:

```python
import numpy as np, pandas as pd
from streamlit.testing.v1 import AppTest


def series(n=400):
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=n)
    close = 100 * 1.004 ** np.arange(1, n + 1); prev = close / 1.004
    return pd.DataFrame({"Open": prev, "High": prev * 1.012, "Low": prev * 0.995, "Close": close, "Volume": 5e6}, index=idx)


def test_desk_page_renders_and_checks_a_trade(monkeypatch, tmp_path):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    from nse_backtest.desk import loaders
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: series())
    monkeypatch.setattr(loaders, "load_regime", lambda p: series())
    monkeypatch.setattr(loaders, "next_earnings", lambda p, s: (None, True))
    monkeypatch.setattr(loaders.nse_lists, "fetch_fno_ban",
                        lambda d, t, fetcher=None: loaders.nse_lists.BanList(frozenset(), "ok", None, ""))
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Today's Plan", "Check a Trade", "My Positions", "Intraday"]
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    assert not at.exception
    assert any(w in " ".join(m.value for m in at.markdown) for w in ("TAKE", "WAIT", "SKIP"))
```

- [ ] Steps 2–5 (+ full gate green, including the fixed stale test). Commit `feat(ui): trade desk dashboard page with four tabs and market switch`.

---

### Task 10: ECC review round + fixes + docs

- [ ] Dispatch in parallel the three vendored reviewers (`.claude/agents/python-reviewer.md`, `security-reviewer.md`, `silent-failure-hunter.md`) over `git diff feat/l1-pipeline-pwa...HEAD -- nse_backtest/desk pages/0_Desk.py`.
- [ ] Verify each finding against the code; fix confirmed ones with a regression test each; commit `fix(desk): ECC review round 1`.
- [ ] Add a "Trade Desk" section to `README.md` and `USAGE_TOMORROW.md` (how to open the page, CLI examples with fictional numbers, what TAKE/WAIT/SKIP mean, disclaimer). Commit `docs: trade desk usage`.
- [ ] Final gate, then merge `feat/trade-desk` into local `main` (`git merge --no-ff`), run the gate on `main`, do not push.
