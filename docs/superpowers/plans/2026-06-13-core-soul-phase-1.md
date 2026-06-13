# Core Soul — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the bagholder-defense system and the picker-replay backtest so the user can see, by end of week 2, what every GO pick over 2023–2025 would have returned.

**Architecture:** Keep `analyze_swing(df, symbol, capital, risk_pct) → TradeSetup` as the stable seam. Phase 1 swaps the v1 engine internals: drops the silent backtest dimension, adds a momentum-decay penalty, adds break-even/trail-SL and time-stop exits, and adds a new `position_monitor` daily re-score path. A new `picker_replay` module walks history through `analyze_swing` and emits per-trade outcomes. Two new pages: Decay Watch (held positions ranked by re-score) and a Picker Replay tab on the existing Backtest page.

**Tech Stack:** Python 3.11, pandas, numpy, `ta`, Streamlit, pytest. yfinance for data (already wired through `nse_backtest/data.py::fetch_nse` / `fetch_multiple`).

**Spec reference:** `docs/superpowers/specs/2026-06-13-core-soul-redesign-design.md` (sections 5.1–5.7, 8.2)

---

## File Structure

**Created:**
- `nse_backtest/exits.py` — `trail_stop_after_t1()`, `time_stop_triggered()`, exit-reason constants
- `nse_backtest/position_monitor.py` — `ReScoreVerdict` dataclass + `daily_check()` function
- `nse_backtest/picker_replay.py` — `BacktestReport` + `replay_picker()` + `simulate_trade()`
- `pages/12_Decay_Watch.py` — held positions ranked by re-score
- `tests/test_exits.py`, `tests/test_position_monitor.py`, `tests/test_picker_replay.py`, `tests/test_phase1_scorer_changes.py`

**Modified:**
- `nse_backtest/scorer.py` — drop backtest dimension, renormalize weights, add momentum-decay penalty
- `nse_backtest/trading_modes.py::analyze_swing` — pass through new exit fields (optional, for re-score integration)
- `pages/6_Backtest.py` — add "Picker Replay" tab

**Untouched:** `pages/7_Positions.py` (MTF calculator stays as-is), `nse_backtest/engine.py` (strategy backtest path unchanged).

**Spec divergence noted:** The spec (§5.3, §8.2) calls for re-score badges on `pages/7_Positions.py`. Exploration revealed that file is an MTF *interest calculator*, not a held-positions list — `state.positions` is written from Picks but never rendered. Rather than entangle the MTF tool with a held-positions list, Phase 1 ships the re-score surface as the new `pages/12_Decay_Watch.py` (which becomes the de facto held-positions view). The MTF calculator stays untouched on `7_Positions.py`. A Phase 2 task can fold MTF analysis into Decay Watch rows.

---

### Task 1: Drop silent Backtest dimension and renormalize weights

**Files:**
- Modify: `nse_backtest/scorer.py:671-683` (weights block in `analyze_stock`)
- Test: `tests/test_phase1_scorer_changes.py`

- [ ] **Step 1.1: Write the failing test**

```python
# tests/test_phase1_scorer_changes.py
import pandas as pd
from nse_backtest.scorer import analyze_stock


def _bullish_df(n=260):
    import numpy as np
    rng = np.random.default_rng(7)
    base = 100 * (1 + np.linspace(0, 0.6, n)) + rng.normal(0, 0.4, n).cumsum() * 0.3
    return pd.DataFrame({
        "Open": base, "High": base * 1.01, "Low": base * 0.99,
        "Close": base, "Volume": rng.integers(2_000_000, 5_000_000, n),
    }, index=pd.bdate_range("2023-01-02", periods=n))


def test_backtest_dimension_removed_from_runtime_score():
    """run_backtests=False used to silently inject 50/100 for 15% of weight.
    After Phase 1, the backtest dimension is NOT part of the runtime final_score."""
    df = _bullish_df()
    out = analyze_stock(df, "TEST", run_backtests=False)

    # Final score must equal the weighted sum of the FIVE remaining dimensions,
    # using renormalized weights (trend .30, momentum .23, volume .18, volatility .12, risk .17).
    expected = (
        out.trend_score * 0.30
        + out.momentum_score * 0.23
        + out.volume_score * 0.18
        + out.volatility_score * 0.12
        + out.risk_score * 0.17
    )
    assert abs(out.final_score - expected) < 0.5, (
        f"final_score {out.final_score:.2f} does not match renormalized 5-dim sum {expected:.2f}; "
        f"backtest dimension may still be silently contributing."
    )


def test_backtest_score_is_zero_when_skipped():
    """When run_backtests=False, backtest_score stays at 0 (not the old 50 fallback)
    — this makes the bypass observable instead of silently noisy."""
    df = _bullish_df()
    out = analyze_stock(df, "TEST", run_backtests=False)
    assert out.backtest_score == 0
```

- [ ] **Step 1.2: Run the test to verify it fails**

Run: `pytest tests/test_phase1_scorer_changes.py -v`
Expected: FAIL — current code uses 6-dim weights and sets backtest_score=50 when skipped.

- [ ] **Step 1.3: Update `analyze_stock` in `nse_backtest/scorer.py`**

Replace lines `593-595` (the `if run_backtests:` block that sets `backtest_score = 50`):

```python
    if run_backtests:
        result.backtest_score, bt_r = score_backtest(df)
    else:
        result.backtest_score = 0
        bt_r = ["Backtest dimension skipped (Phase 1: runtime weight removed)"]
```

Replace the weights block at lines `671-683`:

```python
    # Phase 1: Backtest dimension dropped from runtime scoring.
    # When run_backtests=True (e.g., Analyze page, batch CLI), backtest_score
    # is still computed and surfaced in the breakdown UI, but it does not
    # contribute to final_score. Phase 2 re-introduces it as a nightly cache.
    if run_backtests and result.backtest_score > 0:
        weights = {
            "trend": 0.25, "momentum": 0.20, "volume": 0.15,
            "volatility": 0.10, "backtest": 0.15, "risk": 0.15,
        }
        result.final_score = (
            result.trend_score * weights["trend"]
            + result.momentum_score * weights["momentum"]
            + result.volume_score * weights["volume"]
            + result.volatility_score * weights["volatility"]
            + result.backtest_score * weights["backtest"]
            + result.risk_score * weights["risk"]
        )
    else:
        # Phase 1 runtime weights — renormalized after dropping backtest dim.
        weights = {"trend": 0.30, "momentum": 0.23, "volume": 0.18, "volatility": 0.12, "risk": 0.17}
        assert abs(sum(weights.values()) - 1.0) < 1e-9
        result.final_score = (
            result.trend_score * weights["trend"]
            + result.momentum_score * weights["momentum"]
            + result.volume_score * weights["volume"]
            + result.volatility_score * weights["volatility"]
            + result.risk_score * weights["risk"]
        )
```

- [ ] **Step 1.4: Run tests to verify pass**

Run: `pytest tests/test_phase1_scorer_changes.py -v && pytest tests/test_scorer.py -v`
Expected: PASS. Existing `test_scorer.py` should still pass because `test_analyze_stock_full_pipeline` uses `run_backtests=True` and the GO/WAIT/AVOID assertions remain valid.

- [ ] **Step 1.5: Commit**

```bash
git add nse_backtest/scorer.py tests/test_phase1_scorer_changes.py
git commit -m "fix: drop silent Backtest dimension from runtime scorer (Phase 1)

The 'Backtest' dimension was contributing a constant 50/100 in the
Picks production path (analyze_swing → run_backtests=False), silently
poisoning 15% of every verdict. Phase 1 renormalizes runtime weights
across the 5 remaining dimensions and surfaces backtest_score=0 to make
the bypass observable. Backtest re-enters in Phase 2 as nightly cache."
```

---

### Task 2: Momentum-decay penalty

**Files:**
- Modify: `nse_backtest/scorer.py::score_momentum` (after the ROC block, lines ~195-202)
- Test: `tests/test_phase1_scorer_changes.py`

- [ ] **Step 2.1: Write the failing test**

Append to `tests/test_phase1_scorer_changes.py`:

```python
import numpy as np


def test_momentum_decay_penalty_fires_on_flatlined_uptrend():
    """A stock that ran up hard for 60 bars then went sideways for 5 bars
    should get a momentum penalty — that's the bagholder signal."""
    n = 260
    rng = np.random.default_rng(42)
    # 255 bars of strong uptrend, then 5 bars flat
    trend = np.linspace(100, 180, 255)
    flat = np.full(5, 180.0)
    close = np.concatenate([trend, flat]) + rng.normal(0, 0.3, n)
    df = pd.DataFrame({
        "Open": close, "High": close * 1.005, "Low": close * 0.995,
        "Close": close,
        "Volume": np.concatenate([rng.integers(3_000_000, 5_000_000, 255),
                                  rng.integers(800_000, 1_200_000, 5)]),  # volume dies too
    }, index=pd.bdate_range("2023-01-02", periods=n))

    from nse_backtest.scorer import score_momentum
    s, reasons = score_momentum(df)
    assert any("Momentum decaying" in r for r in reasons), (
        f"expected decay reason; got reasons={reasons}"
    )


def test_momentum_decay_does_not_fire_on_clean_uptrend():
    """A still-accelerating uptrend must NOT get the penalty."""
    n = 260
    rng = np.random.default_rng(11)
    close = np.linspace(100, 200, n) + rng.normal(0, 0.3, n)
    df = pd.DataFrame({
        "Open": close, "High": close * 1.005, "Low": close * 0.995,
        "Close": close,
        "Volume": rng.integers(3_000_000, 5_000_000, n),
    }, index=pd.bdate_range("2023-01-02", periods=n))

    from nse_backtest.scorer import score_momentum
    s, reasons = score_momentum(df)
    assert not any("Momentum decaying" in r for r in reasons)
```

- [ ] **Step 2.2: Run tests to verify fail**

Run: `pytest tests/test_phase1_scorer_changes.py -v -k momentum_decay`
Expected: FAIL — penalty not implemented.

- [ ] **Step 2.3: Add the penalty to `score_momentum`**

In `nse_backtest/scorer.py`, locate `score_momentum` (line ~134). Just before the `return min(score, 100), reasons` line, add:

```python
    # --- Phase 1: Momentum-decay penalty (bagholder defense) ---
    # If both 5-bar ROC and 5-bar OBV slope have flattened to <30% of their
    # 20-bar counterparts, the trend is rolling over even though absolute
    # levels still look healthy. This is the bagholder fingerprint.
    if len(close) >= 25:
        roc5 = momentum.ROCIndicator(close, window=5).roc().iloc[-1]
        roc20 = momentum.ROCIndicator(close, window=20).roc().iloc[-1]
        if pd.notna(roc5) and pd.notna(roc20) and abs(roc20) > 0.1:
            roc_ratio = abs(roc5) / abs(roc20)
        else:
            roc_ratio = 1.0

        obv_series = volume.OnBalanceVolumeIndicator(close, df["Volume"]).on_balance_volume()
        if len(obv_series) >= 20:
            obv5 = obv_series.iloc[-5:].dropna()
            obv20 = obv_series.iloc[-20:].dropna()
            if len(obv5) >= 2 and len(obv20) >= 2:
                slope5 = np.polyfit(np.arange(len(obv5)), obv5.values, 1)[0]
                slope20 = np.polyfit(np.arange(len(obv20)), obv20.values, 1)[0]
                if abs(slope20) > 1:
                    obv_ratio = abs(slope5) / abs(slope20)
                else:
                    obv_ratio = 1.0
            else:
                obv_ratio = 1.0
        else:
            obv_ratio = 1.0

        if roc_ratio < 0.3 and obv_ratio < 0.2:
            score = max(score - 25, 0)
            reasons.append("⚠️ Momentum decaying — ROC and OBV both flatlining (bagholder risk)")
```

- [ ] **Step 2.4: Run tests to verify pass**

Run: `pytest tests/test_phase1_scorer_changes.py -v && pytest tests/test_scorer.py -v`
Expected: PASS for all.

- [ ] **Step 2.5: Commit**

```bash
git add nse_backtest/scorer.py tests/test_phase1_scorer_changes.py
git commit -m "feat: momentum-decay penalty in score_momentum (Phase 1)

Detects bagholder fingerprint: stock looks strong on absolute levels
but 5d ROC and 5d OBV slope have both flatlined to <30%/<20% of their
20d counterparts. Penalizes momentum score by 25 points and surfaces
a warning reason. Owner: Priya Iyer (A.2)."
```

---

### Task 3: `exits.py` — break-even/trail-SL and time-stop primitives

**Files:**
- Create: `nse_backtest/exits.py`
- Test: `tests/test_exits.py`

- [ ] **Step 3.1: Write the failing test**

```python
# tests/test_exits.py
import pandas as pd
import pytest
from nse_backtest.exits import (
    update_trail_stop, time_stop_triggered, ExitReason,
)


def test_trail_stop_moves_to_breakeven_when_t1_hit():
    entry, sl, t1, atr = 100.0, 95.0, 110.0, 2.0
    # On the bar T1 was hit, new SL must be at entry (break-even).
    new_sl, partial = update_trail_stop(
        entry=entry, current_sl=sl, t1=t1, atr=atr,
        bar_high=111.0, bar_low=108.0, last_swing_low=104.0,
        t1_hit_already=False,
    )
    assert new_sl == pytest.approx(entry)
    assert partial is True  # take 50% off the table


def test_trail_stop_trails_above_breakeven_on_new_highs():
    """Once T1 already hit, SL ratchets up using max(1.5*ATR, swing_low - 0.3*ATR)."""
    entry, sl, t1, atr = 100.0, 100.0, 110.0, 2.0  # SL already at BE
    new_sl, partial = update_trail_stop(
        entry=entry, current_sl=sl, t1=t1, atr=atr,
        bar_high=120.0, bar_low=117.0, last_swing_low=115.0,
        t1_hit_already=True,
    )
    # max(120 - 1.5*2, 115 - 0.3*2) = max(117.0, 114.4) = 117.0
    assert new_sl == pytest.approx(117.0)
    assert partial is False  # second-and-onward updates don't trigger a partial


def test_trail_stop_never_goes_backwards():
    """SL must monotonically increase once T1 hit."""
    new_sl, _ = update_trail_stop(
        entry=100.0, current_sl=115.0, t1=110.0, atr=2.0,
        bar_high=118.0, bar_low=117.5, last_swing_low=112.0,
        t1_hit_already=True,
    )
    # max(118 - 3, 112 - 0.6) = max(115.0, 111.4) = 115.0, equal to current — don't drop
    assert new_sl >= 115.0


def test_time_stop_fires_when_held_long_with_decayed_score():
    """>12 bars held + current re-score <50 + price below entry → exit."""
    assert time_stop_triggered(bars_held=13, current_rescore=42, entry_price=100, current_price=98)


def test_time_stop_does_not_fire_when_above_entry():
    assert not time_stop_triggered(bars_held=20, current_rescore=42, entry_price=100, current_price=105)


def test_time_stop_does_not_fire_when_score_still_healthy():
    assert not time_stop_triggered(bars_held=20, current_rescore=58, entry_price=100, current_price=98)


def test_exit_reason_strings_are_stable():
    """These strings appear in the trade journal — changing them silently breaks history filters."""
    assert ExitReason.TARGET_1_PARTIAL == "T1_PARTIAL_BE"
    assert ExitReason.TRAIL_STOP == "TRAIL_STOP"
    assert ExitReason.TIME_STOP == "TIME_STOP"
    assert ExitReason.STOP_LOSS == "STOP_LOSS"
    assert ExitReason.TARGET_2 == "TARGET_2"
```

- [ ] **Step 3.2: Run tests to verify fail**

Run: `pytest tests/test_exits.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3.3: Implement `nse_backtest/exits.py`**

```python
"""Exit primitives for swing/positional engines.

Phase 1 introduces:
  - update_trail_stop(): break-even-and-trail after T1, monotonic SL.
  - time_stop_triggered(): bagholder kill switch.

These are pure functions — no I/O, no state — so they're trivially testable
and reusable from both live (position_monitor) and replay (picker_replay).
"""
from __future__ import annotations

from typing import Final


class ExitReason:
    """Stable string constants — these end up in the trade journal."""
    TARGET_1_PARTIAL: Final[str] = "T1_PARTIAL_BE"
    TARGET_2: Final[str] = "TARGET_2"
    TRAIL_STOP: Final[str] = "TRAIL_STOP"
    STOP_LOSS: Final[str] = "STOP_LOSS"
    TIME_STOP: Final[str] = "TIME_STOP"
    SCORE_DECAY: Final[str] = "SCORE_DECAY"
    END_OF_REPLAY: Final[str] = "END_OF_REPLAY"


def update_trail_stop(
    *,
    entry: float,
    current_sl: float,
    t1: float,
    atr: float,
    bar_high: float,
    bar_low: float,
    last_swing_low: float,
    t1_hit_already: bool,
) -> tuple[float, bool]:
    """Compute the new trailing stop and whether a partial-exit should fire.

    Rules:
      - If T1 not yet hit and bar's high >= T1: SL jumps to entry (break-even),
        and the caller takes 50% off.
      - If T1 already hit: SL ratchets to max(bar_high - 1.5*ATR, swing_low - 0.3*ATR).
      - SL is monotonic post-T1: never decreases.

    Returns:
      (new_sl, take_partial_now)
    """
    if not t1_hit_already and bar_high >= t1:
        return entry, True

    if t1_hit_already:
        candidate = max(bar_high - 1.5 * atr, last_swing_low - 0.3 * atr)
        return max(current_sl, candidate), False

    return current_sl, False


def time_stop_triggered(
    *, bars_held: int, current_rescore: float, entry_price: float, current_price: float,
    max_bars: int = 12, decay_threshold: float = 50.0,
) -> bool:
    """True when the bagholder kill-switch should fire.

    All three must hold:
      - held more than max_bars bars
      - re-score has decayed below decay_threshold
      - price is still below entry (we're underwater)
    """
    return (
        bars_held > max_bars
        and current_rescore < decay_threshold
        and current_price < entry_price
    )
```

- [ ] **Step 3.4: Run tests**

Run: `pytest tests/test_exits.py -v`
Expected: 7 PASS.

- [ ] **Step 3.5: Commit**

```bash
git add nse_backtest/exits.py tests/test_exits.py
git commit -m "feat: exits.py — break-even/trail-SL + time-stop primitives (Phase 1)

Pure functions used by position_monitor (live) and picker_replay (backtest).
update_trail_stop() takes 50% off at T1 and ratchets monotonically.
time_stop_triggered() fires the bagholder kill switch when held >12 bars
with decayed re-score and underwater price. Owner: Vikram Rao (B.6) + Anita Desai (B.7)."
```

---

### Task 4: `position_monitor.py` — daily re-score for held positions

**Files:**
- Create: `nse_backtest/position_monitor.py`
- Test: `tests/test_position_monitor.py`

- [ ] **Step 4.1: Write the failing test**

```python
# tests/test_position_monitor.py
import pandas as pd
import numpy as np
import pytest
from nse_backtest.position_monitor import daily_check, ReScoreVerdict, ReScoreAction


def _strong_uptrend_df(n=260):
    rng = np.random.default_rng(3)
    base = np.linspace(100, 180, n) + rng.normal(0, 0.4, n)
    return pd.DataFrame({
        "Open": base, "High": base * 1.01, "Low": base * 0.99,
        "Close": base, "Volume": rng.integers(2_000_000, 5_000_000, n),
    }, index=pd.bdate_range("2023-01-02", periods=n))


def _flat_then_drop_df(n=260):
    rng = np.random.default_rng(4)
    up = np.linspace(100, 150, 200)
    drop = np.linspace(150, 120, 60)
    close = np.concatenate([up, drop]) + rng.normal(0, 0.3, n)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": rng.integers(1_000_000, 3_000_000, n),
    }, index=pd.bdate_range("2023-01-02", periods=n))


def test_hold_verdict_on_strong_uptrend():
    df = _strong_uptrend_df()
    position = {
        "symbol": "DEMO_UP", "buy_price": 130.0, "qty": 100,
        "stop_loss": 120.0, "target": 160.0,
        "entry_date": (df.index[-30]).strftime("%Y-%m-%d"),
    }
    verdict = daily_check(position, df)
    assert isinstance(verdict, ReScoreVerdict)
    assert verdict.action in (ReScoreAction.HOLD, ReScoreAction.TIGHTEN_STOP)
    assert 0 <= verdict.current_rescore <= 100
    assert verdict.bars_held > 0


def test_exit_verdict_on_decayed_position():
    df = _flat_then_drop_df()
    position = {
        "symbol": "DEMO_DOWN", "buy_price": 148.0, "qty": 100,
        "stop_loss": 140.0, "target": 165.0,
        "entry_date": (df.index[-50]).strftime("%Y-%m-%d"),
    }
    verdict = daily_check(position, df)
    assert verdict.action == ReScoreAction.EXIT
    assert "decay" in verdict.reason.lower() or "time-stop" in verdict.reason.lower() or "below entry" in verdict.reason.lower()


def test_missing_entry_date_is_treated_as_today():
    """If a saved position lacks entry_date, treat bars_held=0 and never time-stop."""
    df = _strong_uptrend_df()
    position = {"symbol": "X", "buy_price": 130.0, "qty": 100,
                "stop_loss": 120.0, "target": 160.0}
    verdict = daily_check(position, df)
    assert verdict.bars_held == 0
    assert verdict.action != ReScoreAction.EXIT or "time-stop" not in verdict.reason.lower()
```

- [ ] **Step 4.2: Run tests to verify fail**

Run: `pytest tests/test_position_monitor.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 4.3: Implement `nse_backtest/position_monitor.py`**

```python
"""Daily position re-score — the bagholder antidote.

For each held position, runs analyze_swing on today's data and emits one of:
  HOLD          — score still healthy
  TIGHTEN_STOP  — score slipping; recommend pulling SL closer
  EXIT          — score decayed or time-stop fired

The verdict surfaces on pages/12_Decay_Watch.py with one row per position,
sorted worst-first.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final
import pandas as pd

from .trading_modes import analyze_swing
from .exits import time_stop_triggered


class ReScoreAction:
    HOLD: Final[str] = "HOLD"
    TIGHTEN_STOP: Final[str] = "TIGHTEN_STOP"
    EXIT: Final[str] = "EXIT"


@dataclass
class ReScoreVerdict:
    symbol: str
    action: str
    current_rescore: float
    bars_held: int
    current_price: float
    entry_price: float
    pnl_pct: float
    suggested_sl: float | None
    reason: str


def _bars_held(entry_date_str: str | None, df: pd.DataFrame) -> int:
    if not entry_date_str:
        return 0
    try:
        entry = pd.to_datetime(entry_date_str).normalize()
    except (ValueError, TypeError):
        return 0
    idx = df.index[df.index.normalize() >= entry]
    return len(idx)


def daily_check(position: dict, df: pd.DataFrame) -> ReScoreVerdict:
    """Re-score a held position against today's market data.

    Args:
      position: dict with keys symbol, buy_price, qty, stop_loss, target,
                entry_date (YYYY-MM-DD string, optional).
      df: OHLCV up to and including today for the position's symbol.
    """
    symbol = position["symbol"]
    entry = float(position["buy_price"])
    sl = float(position["stop_loss"])
    cur = float(df["Close"].iloc[-1])
    pnl_pct = (cur / entry - 1.0) * 100 if entry > 0 else 0.0
    bars = _bars_held(position.get("entry_date"), df)

    setup = analyze_swing(df, symbol, capital=100_000, risk_pct=2.0)
    rescore = setup.score

    if time_stop_triggered(
        bars_held=bars, current_rescore=rescore,
        entry_price=entry, current_price=cur,
    ):
        return ReScoreVerdict(
            symbol=symbol, action=ReScoreAction.EXIT, current_rescore=rescore,
            bars_held=bars, current_price=cur, entry_price=entry, pnl_pct=pnl_pct,
            suggested_sl=None,
            reason=f"Time-stop fired: held {bars} bars, re-score {rescore:.0f}, underwater {pnl_pct:.1f}%",
        )

    if rescore < 45:
        return ReScoreVerdict(
            symbol=symbol, action=ReScoreAction.EXIT, current_rescore=rescore,
            bars_held=bars, current_price=cur, entry_price=entry, pnl_pct=pnl_pct,
            suggested_sl=None,
            reason=f"Score decay: re-score {rescore:.0f} < 45 — engine no longer endorses this setup",
        )

    if rescore < 60:
        # Tighten SL to the higher of: current SL, suggested SL from re-score
        suggested = max(sl, setup.stop_loss)
        return ReScoreVerdict(
            symbol=symbol, action=ReScoreAction.TIGHTEN_STOP, current_rescore=rescore,
            bars_held=bars, current_price=cur, entry_price=entry, pnl_pct=pnl_pct,
            suggested_sl=suggested,
            reason=f"Re-score slipping ({rescore:.0f}); tighten SL to ₹{suggested:.2f}",
        )

    return ReScoreVerdict(
        symbol=symbol, action=ReScoreAction.HOLD, current_rescore=rescore,
        bars_held=bars, current_price=cur, entry_price=entry, pnl_pct=pnl_pct,
        suggested_sl=sl,
        reason=f"Setup still valid (re-score {rescore:.0f})",
    )
```

- [ ] **Step 4.4: Run tests**

Run: `pytest tests/test_position_monitor.py -v`
Expected: 3 PASS.

- [ ] **Step 4.5: Commit**

```bash
git add nse_backtest/position_monitor.py tests/test_position_monitor.py
git commit -m "feat: position_monitor.daily_check — bagholder antidote (Phase 1)

Re-runs analyze_swing on every held position daily. Emits HOLD /
TIGHTEN_STOP / EXIT with reason text. Surfaces on the new Decay Watch
page. Owner: Anita Desai (B.7)."
```

---

### Task 5: `picker_replay.py` — backtest harness for the picker itself

**Files:**
- Create: `nse_backtest/picker_replay.py`
- Test: `tests/test_picker_replay.py`

- [ ] **Step 5.1: Write the failing test**

```python
# tests/test_picker_replay.py
import pandas as pd
import numpy as np
import pytest
from nse_backtest.picker_replay import (
    replay_picker, simulate_trade, BacktestReport, TradeOutcome,
)


def _winning_df(n=300):
    """A clean uptrend that should hit T1 cleanly."""
    rng = np.random.default_rng(5)
    close = np.linspace(100, 200, n) + rng.normal(0, 0.3, n)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": rng.integers(2_000_000, 5_000_000, n),
    }, index=pd.bdate_range("2023-01-02", periods=n))


def test_simulate_trade_records_t1_hit_and_trail_exit():
    df = _winning_df()
    entry_idx = 240
    entry_date = df.index[entry_idx]
    future = df.iloc[entry_idx:]
    outcome = simulate_trade(
        symbol="TEST",
        entry_date=entry_date,
        entry_price=float(future["Close"].iloc[0]),
        stop_loss=float(future["Close"].iloc[0]) * 0.95,
        target_1=float(future["Close"].iloc[0]) * 1.08,
        target_2=float(future["Close"].iloc[0]) * 1.15,
        atr=float(future["Close"].iloc[0]) * 0.02,
        future_data=future, max_hold=15,
    )
    assert isinstance(outcome, TradeOutcome)
    assert outcome.gross_return_pct > 0  # uptrend → win
    assert outcome.exit_reason in ("T1_PARTIAL_BE", "TRAIL_STOP", "TARGET_2")


def test_replay_picker_handles_empty_universe():
    """No symbols → empty report, not a crash."""
    report = replay_picker(symbol_data={}, start="2024-01-01", end="2024-03-01")
    assert isinstance(report, BacktestReport)
    assert report.total_trades == 0
    assert report.expectancy_pct == 0


def test_replay_picker_on_single_winning_symbol(monkeypatch):
    """Replay one strong-uptrend symbol, expect at least one GO and a positive expectancy."""
    df = _winning_df()
    symbol_data = {"WIN": df}
    report = replay_picker(
        symbol_data=symbol_data,
        start=df.index[200].strftime("%Y-%m-%d"),
        end=df.index[270].strftime("%Y-%m-%d"),
        min_score=55, max_hold=10,
    )
    assert report.total_trades >= 1
    # On a clean uptrend, the picker should win >50% of attempts.
    assert report.win_rate >= 0.5
```

- [ ] **Step 5.2: Run tests to verify fail**

Run: `pytest tests/test_picker_replay.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 5.3: Implement `nse_backtest/picker_replay.py`**

```python
"""Picker-Replay Backtest
========================
Walks history day by day, calls analyze_swing on truncated data
(no look-ahead), simulates the resulting trade plan forward, records
every outcome. The deliverable that proves whether the picker has edge.

Owner: Sandeep Kumar (E.13).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
import numpy as np

from .trading_modes import analyze_swing
from .exits import update_trail_stop, time_stop_triggered, ExitReason


# --- Zerodha delivery cost shorthand (matches engine.TradeConfig defaults) ---
_STT_SELL = 0.001        # 0.1%
_STAMP_BUY = 0.00015     # 0.015%
_SLIPPAGE_ONE_WAY = 0.001  # 0.1%
_DP_PER_SELL = 15.93
_NSE_TXN = 0.0000297
_SEBI = 0.000001
_GST = 0.18
_IPFT = 0.0000001


def _delivery_costs_pct(entry: float, exit_price: float, shares: int) -> float:
    """Return total round-trip Zerodha delivery costs as % of (exit_price * shares)."""
    if shares <= 0 or exit_price <= 0:
        return 0.0
    buy_turnover = entry * shares
    sell_turnover = exit_price * shares
    stt = sell_turnover * _STT_SELL
    stamp = buy_turnover * _STAMP_BUY
    txn = (buy_turnover + sell_turnover) * (_NSE_TXN + _SEBI + _IPFT)
    gst = txn * _GST
    dp = _DP_PER_SELL
    slip = (buy_turnover + sell_turnover) * _SLIPPAGE_ONE_WAY
    total = stt + stamp + txn + gst + dp + slip
    return total / sell_turnover * 100


@dataclass
class TradeOutcome:
    symbol: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float           # blended if partial exit at T1
    bars_held: int
    gross_return_pct: float
    net_return_pct: float       # after Zerodha delivery costs + slippage
    exit_reason: str
    score_at_entry: float
    win_probability_at_entry: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class BacktestReport:
    trades: list[TradeOutcome] = field(default_factory=list)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t.net_return_pct > 0) / len(self.trades)

    @property
    def avg_win_pct(self) -> float:
        wins = [t.net_return_pct for t in self.trades if t.net_return_pct > 0]
        return float(np.mean(wins)) if wins else 0.0

    @property
    def avg_loss_pct(self) -> float:
        losses = [t.net_return_pct for t in self.trades if t.net_return_pct <= 0]
        return float(np.mean(losses)) if losses else 0.0

    @property
    def expectancy_pct(self) -> float:
        if not self.trades:
            return 0.0
        wr = self.win_rate
        return wr * self.avg_win_pct + (1 - wr) * self.avg_loss_pct

    @property
    def profit_factor(self) -> float:
        wins = sum(t.net_return_pct for t in self.trades if t.net_return_pct > 0)
        losses = -sum(t.net_return_pct for t in self.trades if t.net_return_pct < 0)
        return wins / losses if losses > 0 else float("inf") if wins > 0 else 0.0

    def to_dataframe(self) -> pd.DataFrame:
        rows = []
        for t in self.trades:
            rows.append({
                "symbol": t.symbol,
                "entry_date": t.entry_date.strftime("%Y-%m-%d"),
                "exit_date": t.exit_date.strftime("%Y-%m-%d"),
                "entry_price": round(t.entry_price, 2),
                "exit_price": round(t.exit_price, 2),
                "bars_held": t.bars_held,
                "gross_%": round(t.gross_return_pct, 2),
                "net_%": round(t.net_return_pct, 2),
                "exit_reason": t.exit_reason,
                "score": round(t.score_at_entry, 1),
                "win_prob": round(t.win_probability_at_entry, 1),
            })
        return pd.DataFrame(rows)


def simulate_trade(
    *,
    symbol: str,
    entry_date: pd.Timestamp,
    entry_price: float,
    stop_loss: float,
    target_1: float,
    target_2: float,
    atr: float,
    future_data: pd.DataFrame,
    max_hold: int = 15,
) -> Optional[TradeOutcome]:
    """Simulate a single trade forward through `future_data` (which starts at entry bar).

    Returns None when there's no future data to simulate against.
    """
    if len(future_data) < 2:
        return None

    sl = stop_loss
    t1_hit = False
    partial_exit_price = 0.0  # price when 50% taken
    partial_shares_pct = 0.0  # 0.5 once T1 hit, else 0

    for i in range(1, min(len(future_data), max_hold + 1)):
        bar = future_data.iloc[i]
        bar_high = float(bar["High"])
        bar_low = float(bar["Low"])

        # Check T2 first (full exit on target)
        if bar_high >= target_2:
            exit_price = target_2
            # Blend with partial if applicable
            if t1_hit:
                exit_price = partial_exit_price * 0.5 + target_2 * 0.5
            return _build_outcome(
                symbol, entry_date, future_data.index[i], entry_price, exit_price,
                i, ExitReason.TARGET_2, atr,
            )

        # Check stop / trail
        if bar_low <= sl:
            exit_price = sl
            if t1_hit:
                exit_price = partial_exit_price * 0.5 + sl * 0.5
            reason = ExitReason.TRAIL_STOP if t1_hit else ExitReason.STOP_LOSS
            return _build_outcome(
                symbol, entry_date, future_data.index[i], entry_price, exit_price,
                i, reason, atr,
            )

        # Update trail / detect T1
        swing_low = float(future_data["Low"].iloc[max(0, i - 5):i + 1].min())
        new_sl, take_partial = update_trail_stop(
            entry=entry_price, current_sl=sl, t1=target_1, atr=atr,
            bar_high=bar_high, bar_low=bar_low, last_swing_low=swing_low,
            t1_hit_already=t1_hit,
        )
        if take_partial and not t1_hit:
            t1_hit = True
            partial_exit_price = target_1
            partial_shares_pct = 0.5
        sl = new_sl

    # End of max_hold — exit at the last close
    last_close = float(future_data["Close"].iloc[min(max_hold, len(future_data) - 1)])
    exit_price = last_close
    if t1_hit:
        exit_price = partial_exit_price * 0.5 + last_close * 0.5
    reason = ExitReason.TARGET_1_PARTIAL if t1_hit else ExitReason.END_OF_REPLAY
    return _build_outcome(
        symbol, entry_date,
        future_data.index[min(max_hold, len(future_data) - 1)],
        entry_price, exit_price, min(max_hold, len(future_data) - 1), reason, atr,
    )


def _build_outcome(symbol, entry_date, exit_date, entry, exit_price, bars,
                   reason, atr) -> TradeOutcome:
    gross = (exit_price / entry - 1) * 100 if entry > 0 else 0.0
    # Assume 100 shares for cost-% calculation; the percentage is invariant to size
    # in the limit (per-share costs are negligible at typical Nifty prices).
    costs_pct = _delivery_costs_pct(entry, exit_price, shares=100)
    net = gross - costs_pct
    return TradeOutcome(
        symbol=symbol, entry_date=entry_date, exit_date=exit_date,
        entry_price=entry, exit_price=exit_price, bars_held=bars,
        gross_return_pct=gross, net_return_pct=net, exit_reason=reason,
        score_at_entry=0.0, win_probability_at_entry=0.0,
    )


def replay_picker(
    *,
    symbol_data: dict[str, pd.DataFrame],
    start: str,
    end: str,
    min_score: float = 65,
    max_hold: int = 15,
    capital: float = 100_000,
    risk_pct: float = 2.0,
    one_position_per_symbol: bool = True,
) -> BacktestReport:
    """Walk every trading day in [start, end], replay analyze_swing on truncated data."""
    report = BacktestReport()
    if not symbol_data:
        return report

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    all_dates = sorted({d for df in symbol_data.values() for d in df.index
                        if start_ts <= d <= end_ts})

    open_until: dict[str, pd.Timestamp] = {}  # symbol -> exit date of current trade

    for d in all_dates:
        for sym, df in symbol_data.items():
            if one_position_per_symbol and open_until.get(sym, pd.Timestamp.min) >= d:
                continue
            df_until = df.loc[:d]
            if len(df_until) < 60:  # need history for indicators
                continue
            try:
                setup = analyze_swing(df_until, sym, capital, risk_pct)
            except Exception:
                continue
            if setup.signal != "BUY" or setup.score < min_score:
                continue

            atr_proxy = (setup.entry_price - setup.stop_loss) / 1.5  # invert SL=entry-1.5*ATR
            future = df.loc[d:]
            outcome = simulate_trade(
                symbol=sym, entry_date=d,
                entry_price=setup.entry_price, stop_loss=setup.stop_loss,
                target_1=setup.target_1, target_2=setup.target_2,
                atr=max(atr_proxy, 0.01), future_data=future, max_hold=max_hold,
            )
            if outcome is None:
                continue
            outcome.score_at_entry = setup.score
            outcome.win_probability_at_entry = setup.win_probability
            outcome.reasons = setup.reasons[:5]
            report.trades.append(outcome)
            open_until[sym] = outcome.exit_date

    return report
```

- [ ] **Step 5.4: Run tests**

Run: `pytest tests/test_picker_replay.py -v`
Expected: 3 PASS.

- [ ] **Step 5.5: Commit**

```bash
git add nse_backtest/picker_replay.py tests/test_picker_replay.py
git commit -m "feat: picker_replay — backtest harness for the picker itself (Phase 1)

Walks history, calls analyze_swing on truncated data with no look-ahead,
simulates each GO pick forward with break-even/trail-SL and time-stop,
reports per-trade outcomes net of Zerodha delivery costs. This is what
produces the user-facing snapshots. Owner: Sandeep Kumar (E.13)."
```

---

### Task 6: New page — Decay Watch

**Files:**
- Create: `pages/12_Decay_Watch.py`
- (No test — Streamlit page tests in this repo are covered by `tests/test_ui_smoke.py` which imports and renders pages without assertions on visible output.)

- [ ] **Step 6.1: Implement the page**

```python
# pages/12_Decay_Watch.py
"""Decay Watch — the bagholder antidote page.

Shows every held position with today's re-score, sorted worst-first.
HOLD / TIGHTEN_STOP / EXIT badges. One-glance "what should I exit today?".
"""
import streamlit as st
import pandas as pd
from components import theme, state
from components.market_data import get_live_price
from nse_backtest.data import fetch_nse
from nse_backtest.position_monitor import daily_check, ReScoreAction

st.set_page_config(page_title="Decay Watch | Trading Lab", page_icon="⚠️", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# ⚠️ Decay Watch")
st.markdown("_Re-score every held position against today's market. Worst first._")

positions = state.get_positions()
if not positions:
    st.info("No positions yet. Open the **Picks** page and save trades to populate this watch.")
    st.stop()

verdicts = []
with st.spinner(f"Re-scoring {len(positions)} positions…"):
    for pos in positions:
        sym = pos.get("symbol", "")
        if not sym:
            continue
        try:
            df = fetch_nse(sym, start="2022-01-01")
        except Exception as e:
            st.warning(f"{sym}: data fetch failed ({e})")
            continue
        if df is None or len(df) < 60:
            continue
        try:
            v = daily_check(pos, df)
            verdicts.append((v, pos))
        except Exception as e:
            st.warning(f"{sym}: re-score failed ({e})")

# Sort worst-first: EXIT > TIGHTEN_STOP > HOLD, then by re-score ascending
_action_order = {ReScoreAction.EXIT: 0, ReScoreAction.TIGHTEN_STOP: 1, ReScoreAction.HOLD: 2}
verdicts.sort(key=lambda vp: (_action_order.get(vp[0].action, 9), vp[0].current_rescore))

if not verdicts:
    st.info("No re-scores produced. Check data availability for your positions.")
    st.stop()

color_map = {
    ReScoreAction.EXIT: "#FF4D4D",
    ReScoreAction.TIGHTEN_STOP: "#FFB800",
    ReScoreAction.HOLD: "#00FF87",
}

for v, pos in verdicts:
    clr = color_map.get(v.action, "#7A93AA")
    st.markdown(
        f'<div style="border:1px solid {clr};border-radius:14px;padding:18px 20px;'
        f'margin:10px 0;background:#0D1526">'
        f'<span style="font-size:22px;font-weight:700;color:{clr}">{v.symbol}</span>'
        f'<span style="font-size:14px;color:#7A93AA;margin-left:12px">'
        f'{v.action} · re-score {v.current_rescore:.0f}/100 · held {v.bars_held} bars</span>'
        f'</div>', unsafe_allow_html=True)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Entry", f"₹{v.entry_price:,.2f}")
    m2.metric("Current", f"₹{v.current_price:,.2f}", delta=f"{v.pnl_pct:+.1f}%",
              delta_color="normal" if v.pnl_pct >= 0 else "inverse")
    if v.suggested_sl is not None:
        m3.metric("Suggested SL", f"₹{v.suggested_sl:,.2f}")
    else:
        m3.metric("Suggested SL", "—")
    m4.metric("Verdict", v.action)
    st.caption(f"💡 {v.reason}")
    st.markdown("---")
```

- [ ] **Step 6.2: Run UI smoke test**

Run: `pytest tests/test_ui_smoke.py -v`
Expected: PASS (the smoke test imports every page).

- [ ] **Step 6.3: Verify manually (one-shot Streamlit run)**

Run: `streamlit run ui.py --server.headless true --server.port 8765 &` then open `http://localhost:8765/Decay_Watch`. Verify the page loads and either shows the empty state or re-scores any saved positions. Kill server with `pkill -f streamlit`.

- [ ] **Step 6.4: Commit**

```bash
git add pages/12_Decay_Watch.py
git commit -m "feat: Decay Watch page — the bagholder antidote UI (Phase 1)

Renders every held position with today's re-score, sorted worst-first.
HOLD/TIGHTEN_STOP/EXIT badges with one-line reason. Owner: Tara Joshi (F.15)."
```

---

### Task 7: Picker Replay tab on the Backtest page

**Files:**
- Modify: `pages/6_Backtest.py` (append new section after the existing strategy backtest block)

- [ ] **Step 7.1: Append the Picker Replay section to `pages/6_Backtest.py`**

Add at the end of the file (no removal of existing strategy backtest UI):

```python
# ════════════════════════════════════════════════════════════════
#  PICKER REPLAY — Phase 1
# ════════════════════════════════════════════════════════════════
from datetime import date as _date
from nse_backtest.data import fetch_multiple, NIFTY50_SYMBOLS
from nse_backtest.picker_replay import replay_picker

st.markdown("---")
st.markdown("## 🔁 Picker Replay")
st.markdown("_Walk history, replay every GO verdict, see what actually happened._")

pr1, pr2, pr3, pr4 = st.columns([2, 2, 2, 1])
pr_from = pr1.date_input("From", value=_date(2024, 1, 1), key="pr_from")
pr_to = pr2.date_input("To", value=_date(2024, 12, 31), key="pr_to")
pr_min_score = pr3.slider("Min score", 50, 90, 65, key="pr_min_score")
pr_max_hold = pr4.number_input("Max hold (bars)", value=15, min_value=5, max_value=60, key="pr_max_hold")

if st.button("🔁  Run Picker Replay (Nifty 50)", use_container_width=True, key="pr_run"):
    with st.spinner("Fetching Nifty 50 history…"):
        symbol_data = fetch_multiple(NIFTY50_SYMBOLS, start="2022-01-01")

    with st.spinner(f"Replaying {pr_from} → {pr_to}…"):
        report = replay_picker(
            symbol_data=symbol_data,
            start=str(pr_from), end=str(pr_to),
            min_score=pr_min_score, max_hold=int(pr_max_hold),
        )

    st.session_state["picker_replay_report"] = report

report = st.session_state.get("picker_replay_report")
if report is not None and report.total_trades > 0:
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Trades", report.total_trades)
    m2.metric("Win rate", f"{report.win_rate * 100:.1f}%")
    m3.metric("Avg win", f"{report.avg_win_pct:+.2f}%")
    m4.metric("Avg loss", f"{report.avg_loss_pct:+.2f}%")
    m5.metric("Expectancy", f"{report.expectancy_pct:+.2f}%")
    pf = report.profit_factor
    st.metric("Profit factor", f"{pf:.2f}" if pf != float("inf") else "∞")

    df_trades = report.to_dataframe()
    st.dataframe(df_trades, use_container_width=True, hide_index=True)

    csv = df_trades.to_csv(index=False).encode()
    st.download_button("📥 Download trades CSV", csv,
                       file_name=f"picker_replay_{pr_from}_to_{pr_to}.csv",
                       mime="text/csv")
elif report is not None:
    st.info("Replay produced zero trades for these filters. Try lowering min score or widening dates.")
```

- [ ] **Step 7.2: Run UI smoke test**

Run: `pytest tests/test_ui_smoke.py -v`
Expected: PASS.

- [ ] **Step 7.3: Commit**

```bash
git add pages/6_Backtest.py
git commit -m "feat: Picker Replay tab on Backtest page (Phase 1)

UI for replay_picker — pick date range + min score + max hold, see
metrics (win rate, avg win/loss, expectancy, profit factor) and the
per-trade table with CSV download. Owner: Sandeep Kumar (E.13) + Karan Malhotra (F.16)."
```

---

### Task 8: Generate the two real snapshots and ship them in the journal

**Files:**
- Create: `scripts/generate_snapshots.py` (one-shot helper, runs and prints)
- Create: `output/snapshots/phase1_picker_replay_2024.csv` (committed evidence)
- Create: `output/snapshots/phase1_top_two.md` (human-readable snapshot doc the user can read)

- [ ] **Step 8.1: Implement `scripts/generate_snapshots.py`**

```python
"""Generate the two real snapshots that close out Phase 1.

Runs replay_picker on Nifty 50 over 2024, sorts trades by net_return_pct,
picks the two clearest winners (highest score-at-entry with positive net return),
and writes a human-readable markdown snapshot per trade.
"""
import os
from pathlib import Path
import pandas as pd

from nse_backtest.data import fetch_multiple, NIFTY50_SYMBOLS
from nse_backtest.picker_replay import replay_picker

OUT_DIR = Path("output/snapshots")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    print("Fetching Nifty 50 history…")
    symbol_data = fetch_multiple(NIFTY50_SYMBOLS, start="2022-01-01")

    print("Replaying 2023-01-01 → 2025-12-31 (spec §5.7 window)…")
    report = replay_picker(
        symbol_data=symbol_data,
        start="2023-01-01", end="2025-12-31",
        min_score=65, max_hold=15,
    )

    df = report.to_dataframe()
    df.to_csv(OUT_DIR / "phase1_picker_replay_2023_2025.csv", index=False)
    print(f"Wrote {len(df)} trades to {OUT_DIR}/phase1_picker_replay_2023_2025.csv")

    # Pick top 2 by (positive net%, then by score-at-entry desc) — clear winners
    winners = df[df["net_%"] > 0].sort_values(
        by=["score", "net_%"], ascending=[False, False]
    ).head(2)

    if len(winners) < 2:
        print("⚠️ Fewer than 2 winners — using top by net_% regardless of sign.")
        winners = df.sort_values(by="net_%", ascending=False).head(2)

    lines = ["# Phase 1 — Two Real Picker-Replay Snapshots (Nifty 50, 2023-2025)\n"]
    lines.append("Generated by `scripts/generate_snapshots.py` from `replay_picker`.")
    lines.append("Net returns are after Zerodha delivery costs (STT, stamp, GST, slippage, DP).\n")

    for i, (_, r) in enumerate(winners.iterrows(), 1):
        lines.append(f"## Snapshot {i} — {r['symbol']} ({r['entry_date']} → {r['exit_date']})\n")
        lines.append("```")
        lines.append(f"Engine verdict at entry: GO  ·  Score {r['score']}/100  ·  Win prob {r['win_prob']}%")
        lines.append(f"Entry         ₹{r['entry_price']:,.2f}")
        lines.append(f"Exit          ₹{r['exit_price']:,.2f}  ({r['exit_reason']})")
        lines.append(f"Held          {r['bars_held']} bars")
        lines.append(f"Gross return  {r['gross_%']:+.2f}%")
        lines.append(f"Net return    {r['net_%']:+.2f}%")
        lines.append("```")
        lines.append("")

    (OUT_DIR / "phase1_top_two.md").write_text("\n".join(lines))
    print(f"Wrote {OUT_DIR}/phase1_top_two.md")
    print("\n--- Summary ---")
    print(f"Total trades: {len(df)}")
    print(f"Win rate    : {report.win_rate * 100:.1f}%")
    print(f"Avg win     : {report.avg_win_pct:+.2f}%")
    print(f"Avg loss    : {report.avg_loss_pct:+.2f}%")
    print(f"Expectancy  : {report.expectancy_pct:+.2f}%")
    print(f"Profit factor: {report.profit_factor:.2f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 8.2: Run the script**

Run: `python scripts/generate_snapshots.py`
Expected: prints "Wrote N trades" and "Wrote .../phase1_top_two.md"; produces both files. Investigate any error before committing.

- [ ] **Step 8.3: Inspect outputs**

Run: `cat output/snapshots/phase1_top_two.md`
Verify: two snapshots formatted with engine verdict, plan, and outcome. The user should be able to read this file and immediately see the two picks.

- [ ] **Step 8.4: Verify runtime budget**

The spec (§5.7) requires `replay_picker` to complete the Nifty 50 × 2023-2025 window in <10 minutes. If `time python scripts/generate_snapshots.py` exceeds that, the most likely culprit is the per-bar `analyze_swing` call (which itself runs all advanced indicators). Mitigation options if needed:
- Skip days where `df_until.index[-1].weekday() != 4` (only re-score on Fridays during replay) — cuts ~5× while preserving the trend.
- Cache `analyze_swing` outputs by `(symbol, date)` in an LRU.
- Profile with `python -m cProfile -s cumulative scripts/generate_snapshots.py | head -40`.

Document the actual runtime in the commit message.

- [ ] **Step 8.5: Commit**

```bash
git add scripts/generate_snapshots.py output/snapshots/phase1_picker_replay_2023_2025.csv output/snapshots/phase1_top_two.md
git commit -m "feat: Phase 1 snapshots — top 2 picker-replay trades from Nifty 50 2023-2025

Closes Phase 1 deliverable. scripts/generate_snapshots.py is the
reproducible source of truth; the .md is the human-readable artifact
the user reads to evaluate trust. Owners: Sandeep Kumar (E.13), Anita Desai (B.7)."
```

---

## Self-Review (run before handing off)

After all tasks land, do a final pass:

- [ ] **Step S.1: Full test suite green**

Run: `pytest -v`
Expected: all 140+ existing tests still pass; new tests in `test_phase1_scorer_changes.py`, `test_exits.py`, `test_position_monitor.py`, `test_picker_replay.py` pass.

- [ ] **Step S.2: Manual UI walk-through**

Run: `./start.sh`
Verify:
- Picks page still works (verdict colors right, sizing right, save flow works).
- Backtest page shows both the existing strategy backtester AND the new Picker Replay section.
- Decay Watch page shows positions if any are saved, empty state otherwise.

- [ ] **Step S.3: Diff sanity**

Run: `git log main..HEAD --oneline`
Expected: 8 commits, one per task, each with a co-authorship trailer.

- [ ] **Step S.4: Update CLAUDE.md / README if needed**

If the README's "How The Scoring Works" table still shows 6 dimensions with the old weights, update it to reflect Phase 1 (5 dimensions runtime, backtest dimension is post-Phase-2 nightly cache). One-line edit; don't rewrite the README.
