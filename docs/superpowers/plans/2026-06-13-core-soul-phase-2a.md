# Core Soul — Phase 2A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `rs_vs_nifty` feature + v2 engine routing seam + A/B replay infrastructure. Prove the Phase 2 pipeline works before bolting on the other 4 features.

**Architecture:** Same stable seam — `analyze_swing(df, symbol, capital, risk_pct) → TradeSetup`. New optional `nifty_df` parameter threads index data through. v1 ignores it; v2 uses it for relative-strength boost. `NSE_SCORER_ENGINE=v2` env var activates the new path. `picker_replay` gains `engine` parameter so A/B is a single-script call.

**Tech stack:** Python, pandas, numpy, pytest. Same as Phase 1.

**Spec reference:** §6 of `docs/superpowers/specs/2026-06-13-core-soul-redesign-design.md`. Phase 1 plan at `docs/superpowers/plans/2026-06-13-core-soul-phase-1.md` shipped at commit `99639b8`. Baseline 157 tests pass.

---

## File structure

**Created:**
- `nse_backtest/features/__init__.py` — exports the booster registry
- `nse_backtest/features/relative_strength.py` — `rs_vs_nifty_boost(stock_df, nifty_df) -> tuple[int, str]`
- `tests/test_relative_strength.py`
- `scripts/ab_replay.py` — runs v1 vs v2 over same window, prints expectancy delta + commits CSV

**Modified:**
- `nse_backtest/trading_modes.py::analyze_swing` — accept optional `nifty_df=None`; pass through
- `nse_backtest/scorer.py::analyze_stock` — read `NSE_SCORER_ENGINE` env var; v2 path applies rs_vs_nifty boost when `nifty_df` is provided
- `nse_backtest/picker_replay.py::replay_picker` — accept optional `nifty_df=None`; pass to `analyze_swing`; `engine: str = "v1"` parameter sets env for the run

---

### Task 1: `rs_vs_nifty_boost` feature

**Files:**
- Create: `nse_backtest/features/__init__.py` (empty or just exports)
- Create: `nse_backtest/features/relative_strength.py`
- Create: `tests/test_relative_strength.py`

- [ ] **Step 1.1: Write failing tests**

```python
# tests/test_relative_strength.py
import pandas as pd
import numpy as np
from nse_backtest.features.relative_strength import rs_vs_nifty_boost


def _df_with_returns(start_price=100.0, returns_pct=None, n=120):
    """Build OHLCV where Close grows by the given list of daily returns_pct."""
    if returns_pct is None:
        returns_pct = [0.005] * n
    prices = [start_price]
    for r in returns_pct:
        prices.append(prices[-1] * (1 + r))
    close = np.array(prices[1:])
    return pd.DataFrame({
        "Open": close, "High": close * 1.005, "Low": close * 0.995,
        "Close": close, "Volume": np.full(n, 1_000_000),
    }, index=pd.bdate_range("2024-01-02", periods=n))


def test_outperforming_stock_gets_boost():
    """Stock returns 1%/day, Nifty returns 0.2%/day → outperforms by >5% on 20d and 60d → +10."""
    stock = _df_with_returns(returns_pct=[0.010] * 120)
    nifty = _df_with_returns(returns_pct=[0.002] * 120)
    boost, reason = rs_vs_nifty_boost(stock, nifty)
    assert boost == 10
    assert "outperform" in reason.lower() or "rs" in reason.lower()


def test_in_line_stock_gets_no_boost():
    """Stock and Nifty both return 0.3%/day → underperform threshold → 0 boost."""
    stock = _df_with_returns(returns_pct=[0.003] * 120)
    nifty = _df_with_returns(returns_pct=[0.003] * 120)
    boost, _ = rs_vs_nifty_boost(stock, nifty)
    assert boost == 0


def test_underperforming_stock_gets_no_boost_and_negative_reason():
    """Stock underperforms Nifty → 0 boost (we don't penalize, just no add)."""
    stock = _df_with_returns(returns_pct=[0.001] * 120)
    nifty = _df_with_returns(returns_pct=[0.005] * 120)
    boost, reason = rs_vs_nifty_boost(stock, nifty)
    assert boost == 0
    assert "underperform" in reason.lower() or "lag" in reason.lower()


def test_missing_nifty_data_returns_zero_boost():
    """If nifty_df has too few bars, return 0 boost and a warning reason — don't crash."""
    stock = _df_with_returns(n=120)
    nifty = _df_with_returns(n=10)
    boost, reason = rs_vs_nifty_boost(stock, nifty)
    assert boost == 0
    assert "insufficient" in reason.lower() or "unavailable" in reason.lower()
```

- [ ] **Step 1.2: Implement `nse_backtest/features/__init__.py`**

```python
"""Phase 2 feature boosters — additive on top of Phase 1 base score."""
```

- [ ] **Step 1.3: Implement `nse_backtest/features/relative_strength.py`**

```python
"""Relative-strength booster: stock returns vs Nifty 50.

Spec §6 (Phase 2): +10 if 20d AND 60d outperformance vs Nifty 50 > +5%.
Owner: Karthik Subramanian (A.3).
"""
from __future__ import annotations

import pandas as pd


def _pct_return(df: pd.DataFrame, bars: int) -> float | None:
    if len(df) < bars + 1:
        return None
    close = df["Close"]
    return (close.iloc[-1] / close.iloc[-1 - bars] - 1.0) * 100


def rs_vs_nifty_boost(stock_df: pd.DataFrame, nifty_df: pd.DataFrame) -> tuple[int, str]:
    """Compute the rs_vs_nifty additive booster (0 or +10) and a reason string.

    Requires both 20d and 60d outperformance >5% to trigger the boost.
    Pure function — caller fetches nifty_df via nse_backtest.data.fetch_nifty50.
    """
    if nifty_df is None or len(nifty_df) < 61:
        return 0, "RS vs Nifty: insufficient nifty data"

    s20 = _pct_return(stock_df, 20)
    n20 = _pct_return(nifty_df, 20)
    s60 = _pct_return(stock_df, 60)
    n60 = _pct_return(nifty_df, 60)

    if None in (s20, n20, s60, n60):
        return 0, "RS vs Nifty: insufficient stock history"

    out20 = s20 - n20
    out60 = s60 - n60

    if out20 > 5.0 and out60 > 5.0:
        return 10, f"RS vs Nifty: outperforming by {out20:.1f}% (20d) / {out60:.1f}% (60d)"
    if out20 < -2.0 or out60 < -2.0:
        return 0, f"RS vs Nifty: lagging ({out20:+.1f}% 20d, {out60:+.1f}% 60d)"
    return 0, f"RS vs Nifty: in-line ({out20:+.1f}% 20d, {out60:+.1f}% 60d)"
```

- [ ] **Step 1.4: Verify tests pass**

`pytest tests/test_relative_strength.py -v` — 4 pass. `pytest -q | tail -3` — 161 total.

- [ ] **Step 1.5: Commit**

```bash
git add nse_backtest/features/ tests/test_relative_strength.py
git commit -m "feat: rs_vs_nifty_boost feature (Phase 2A T1)

Pure-function additive booster: +10 if stock outperforms Nifty by >5%
on both 20d and 60d windows. Returns 0 with explanatory reason when
nifty data is unavailable. Owner: Karthik Subramanian (A.3)."
```

---

### Task 2: v2 engine routing + thread nifty through analyze_swing and picker_replay

**Files:**
- Modify: `nse_backtest/trading_modes.py::analyze_swing` — add `nifty_df=None` parameter
- Modify: `nse_backtest/scorer.py::analyze_stock` — read `NSE_SCORER_ENGINE` env var; apply boost
- Modify: `nse_backtest/picker_replay.py::replay_picker` — accept `nifty_df` and `engine` parameters

- [ ] **Step 2.1: Modify `analyze_swing` in `trading_modes.py`**

Change signature from:
```python
def analyze_swing(df, symbol, capital=100000, risk_pct=2.0) -> TradeSetup:
```
to:
```python
def analyze_swing(df, symbol, capital=100000, risk_pct=2.0, nifty_df=None) -> TradeSetup:
```

In the body, pass `nifty_df=nifty_df` to `analyze_stock`. analyze_stock must accept the new kwarg (next step).

- [ ] **Step 2.2: Modify `analyze_stock` in `scorer.py`**

Add `nifty_df=None` parameter. After the Phase 4 weighted final_score is computed (around line 696, the assert line), but BEFORE the verdict block (around line 699), add v2 boost application:

```python
    # --- Phase 2 features (additive boosters, behind NSE_SCORER_ENGINE=v2) ---
    import os
    if os.getenv("NSE_SCORER_ENGINE", "v1") == "v2":
        from .features.relative_strength import rs_vs_nifty_boost
        if nifty_df is not None:
            rs_boost, rs_reason = rs_vs_nifty_boost(df, nifty_df)
            if rs_boost > 0:
                result.final_score = min(result.final_score + rs_boost, 100)
            adv_reasons.append(rs_reason)
```

- [ ] **Step 2.3: Modify `replay_picker` in `picker_replay.py`**

Change signature to add `nifty_df=None` and `engine="v1"` parameters. Inside the loop, before calling `analyze_swing`, set `os.environ["NSE_SCORER_ENGINE"] = engine`. After the loop, restore the previous value.

Update the call to `analyze_swing` to pass `nifty_df=nifty_df`.

Note: `setup = analyze_swing(df_until, sym, capital, risk_pct)` becomes `setup = analyze_swing(df_until, sym, capital, risk_pct, nifty_df=nifty_df)`.

- [ ] **Step 2.4: Existing tests must still pass**

`pytest -q | tail -3` — 161 still green. `analyze_swing` and `replay_picker` get extra optional kwargs that default to v1 behavior.

- [ ] **Step 2.5: Add an integration test**

Add to `tests/test_relative_strength.py`:

```python
def test_v2_engine_boost_applied_via_analyze_swing(monkeypatch):
    """When NSE_SCORER_ENGINE=v2 and nifty_df is provided, the stock score should
    be at least as high as the v1 score when outperforming."""
    from nse_backtest.trading_modes import analyze_swing

    stock = _df_with_returns(returns_pct=[0.010] * 260)
    nifty = _df_with_returns(returns_pct=[0.002] * 260)

    monkeypatch.setenv("NSE_SCORER_ENGINE", "v1")
    v1 = analyze_swing(stock, "RS_TEST", nifty_df=nifty)

    monkeypatch.setenv("NSE_SCORER_ENGINE", "v2")
    v2 = analyze_swing(stock, "RS_TEST", nifty_df=nifty)

    assert v2.score >= v1.score, f"v2 ({v2.score}) should be >= v1 ({v1.score}) for outperformer"
```

`pytest tests/test_relative_strength.py -v` — 5 pass. Full suite 162.

- [ ] **Step 2.6: Commit**

```bash
git add nse_backtest/trading_modes.py nse_backtest/scorer.py nse_backtest/picker_replay.py tests/test_relative_strength.py
git commit -m "feat: v2 engine routing + nifty threading (Phase 2A T2)

analyze_swing and replay_picker accept optional nifty_df. v2 engine
applies rs_vs_nifty booster when nifty data is provided. Toggle via
NSE_SCORER_ENGINE env var; default v1 behavior preserved."
```

---

### Task 3: A/B replay script

**Files:**
- Create: `scripts/ab_replay.py`

- [ ] **Step 3.1: Implement**

```python
"""A/B replay: v1 baseline vs v2 (with rs_vs_nifty) on identical window.

Prints expectancy delta and writes a CSV per engine for downstream comparison.
"""
import os
from pathlib import Path

import pandas as pd

from nse_backtest.data import fetch_multiple, fetch_nifty50, NIFTY50_SYMBOLS
from nse_backtest.picker_replay import replay_picker

OUT_DIR = Path("output/ab_replay")
OUT_DIR.mkdir(parents=True, exist_ok=True)

START = os.getenv("AB_START", "2024-01-01")
END = os.getenv("AB_END", "2024-12-31")
STRIDE = int(os.getenv("AB_STRIDE", "5"))
MIN_SCORE = float(os.getenv("AB_MIN_SCORE", "65"))


def stride_filter(symbol_data: dict, stride: int) -> dict:
    if stride <= 1:
        return symbol_data
    return {s: df.iloc[::stride].copy() for s, df in symbol_data.items()}


def main():
    print("Fetching Nifty 50 symbols + index…")
    symbol_data = fetch_multiple(NIFTY50_SYMBOLS, start="2022-01-01")
    nifty = fetch_nifty50(start="2022-01-01")
    print(f"  → {len(symbol_data)} symbols, nifty bars: {len(nifty)}")

    sampled = stride_filter(symbol_data, STRIDE)

    print(f"\nReplaying v1 ({START} → {END})…")
    r1 = replay_picker(symbol_data=sampled, start=START, end=END,
                      min_score=MIN_SCORE, max_hold=15, engine="v1")

    print(f"Replaying v2 with rs_vs_nifty…")
    r2 = replay_picker(symbol_data=sampled, start=START, end=END,
                      min_score=MIN_SCORE, max_hold=15,
                      engine="v2", nifty_df=nifty)

    r1.to_dataframe().to_csv(OUT_DIR / f"v1_{START}_{END}.csv", index=False)
    r2.to_dataframe().to_csv(OUT_DIR / f"v2_{START}_{END}.csv", index=False)

    def fmt(r):
        return (f"trades={r.total_trades}, wr={r.win_rate*100:.1f}%, "
                f"avg_win={r.avg_win_pct:+.2f}%, avg_loss={r.avg_loss_pct:+.2f}%, "
                f"expectancy={r.expectancy_pct:+.2f}%, pf={r.profit_factor:.2f}")

    print("\n=== A/B RESULTS ===")
    print(f"v1: {fmt(r1)}")
    print(f"v2: {fmt(r2)}")
    delta_e = r2.expectancy_pct - r1.expectancy_pct
    delta_pf = r2.profit_factor - r1.profit_factor
    print(f"\nΔ expectancy: {delta_e:+.2f}% ({'v2 wins' if delta_e > 0 else 'v1 wins or tied'})")
    print(f"Δ profit factor: {delta_pf:+.2f}")

    verdict_path = OUT_DIR / "verdict.md"
    verdict = f"""# A/B Replay Verdict — Phase 2A: rs_vs_nifty

Window: {START} → {END}, stride={STRIDE}, min_score={MIN_SCORE}

| Metric | v1 (baseline) | v2 (rs_vs_nifty) | Δ |
|---|---|---|---|
| Trades | {r1.total_trades} | {r2.total_trades} | {r2.total_trades - r1.total_trades:+d} |
| Win rate | {r1.win_rate*100:.1f}% | {r2.win_rate*100:.1f}% | {(r2.win_rate - r1.win_rate)*100:+.1f}pp |
| Avg win | {r1.avg_win_pct:+.2f}% | {r2.avg_win_pct:+.2f}% | {r2.avg_win_pct - r1.avg_win_pct:+.2f}pp |
| Avg loss | {r1.avg_loss_pct:+.2f}% | {r2.avg_loss_pct:+.2f}% | {r2.avg_loss_pct - r1.avg_loss_pct:+.2f}pp |
| Expectancy | {r1.expectancy_pct:+.2f}% | {r2.expectancy_pct:+.2f}% | **{delta_e:+.2f}pp** |
| Profit factor | {r1.profit_factor:.2f} | {r2.profit_factor:.2f} | {delta_pf:+.2f} |

**Recommendation:** {'Ship rs_vs_nifty as the v2 default — positive expectancy delta.' if delta_e > 0 else 'Do NOT ship rs_vs_nifty as default — neutral or negative delta. Iterate on the boost magnitude/thresholds before re-running.'}
"""
    verdict_path.write_text(verdict)
    print(f"\nWrote {verdict_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3.2: Run it (live data, ~2 minute runtime with stride=5)**

```
cd /Users/racit/PersonalProject/nse-trading-lab && \
  PYTHONPATH=. /opt/homebrew/opt/python@3.13/bin/python3.13 scripts/ab_replay.py 2>&1 | tail -25
```

- [ ] **Step 3.3: Inspect verdict**

```
cat output/ab_replay/verdict.md
```

- [ ] **Step 3.4: Commit (regardless of A/B outcome — the verdict file is the evidence)**

```bash
git add scripts/ab_replay.py output/ab_replay/
git commit -m "feat: A/B replay v1 vs v2 + rs_vs_nifty verdict (Phase 2A T3)

scripts/ab_replay.py runs both engines on the same window and emits
output/ab_replay/verdict.md with metric deltas. The verdict file is
the decision artifact for whether rs_vs_nifty ships as v2 default."
```

---

## Self-review

- [ ] **Step S.1:** Run full suite. `pytest -q | tail -3` — 162 total.
- [ ] **Step S.2:** Confirm v1 picker behavior unchanged by running Phase 1 snapshot script: `PYTHONPATH=. python3.13 scripts/generate_snapshots.py 2>&1 | grep "Win rate\|Expectancy"` — numbers should match the Phase 1 result (45.9% win rate, +2.25% expectancy).
- [ ] **Step S.3:** Confirm the A/B verdict file actually exists at `output/ab_replay/verdict.md`.
- [ ] **Step S.4:** Push to origin.

If v2 delivers positive expectancy delta, Phase 2B can bolt on `sector_momentum`, `breadth_gate`, `event_gate`, `hmm_regime` using the exact same pattern (feature module → v2 boost block → A/B replay).
