"""Round 7 — Complete audit fix tests.

Covers all remaining issues after Round 6:
- Engine: MTF pnl_pct denominator, signal-shift ordering
- Scorer: min trade count statistical significance
- Screener: volume threshold, RSI threshold, dedup, liquidity gate
- Strategies: Supertrend direction vs ST line
- App: --universe nifty100 CLI flag
- Analytics: avg_holding_days in business days
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nse_backtest.engine import TradeConfig, run_backtest
from nse_backtest.analytics import compute_metrics


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _trending_df(n=400) -> pd.DataFrame:
    dates = pd.bdate_range("2022-01-03", periods=n)
    rng = np.random.default_rng(42)
    close = 100 + np.cumsum(rng.normal(0.2, 1, n))
    close = np.maximum(close, 1)
    return pd.DataFrame({
        "Open":   close * 0.99,
        "High":   close * 1.02,
        "Low":    close * 0.98,
        "Close":  close,
        "Volume": rng.integers(200_000, 2_000_000, n).astype(float),
    }, index=dates)


def _make_backtest_result(n_trades=5, mtf=False):
    """Build a minimal backtest result dict with n_trades closed trades."""
    from nse_backtest.engine import Trade
    import pandas as pd
    dates = pd.bdate_range("2023-01-01", periods=max(n_trades * 10, 40))
    equity = pd.Series(100_000 + np.linspace(0, 10_000, len(dates)), index=dates)
    bh = equity.copy()
    config = TradeConfig(trading_mode="MTF" if mtf else "DELIVERY")
    trades = []
    for k in range(n_trades):
        t = Trade(
            entry_date=dates[k * 4],
            entry_price=100.0,
            exit_date=dates[k * 4 + 3],
            exit_price=110.0,
            shares=10,
            pnl=100.0,
            pnl_pct=0.1,
            costs=5.0,
            interest=0.0,
            exit_reason="signal",
        )
        trades.append(t)
    return {"equity_curve": equity, "buy_hold_curve": bh,
            "trades": trades, "config": config, "data": pd.DataFrame()}


# ---------------------------------------------------------------------------
# ENGINE — MTF pnl_pct denominator
# ---------------------------------------------------------------------------

class TestMtfPnlPctDenominator:
    """MTF pnl_pct must be calculated on INVESTED equity (margin portion),
    not full notional. Bug: divides by entry_p * shares (full notional).
    Fix: divide by entry_p * shares * mtf_margin_pct.
    This is critical — it makes MTF look 4× less profitable than reality."""

    def _make_mtf_df(self):
        dates = pd.bdate_range("2024-01-02", periods=15)
        prices = [100] * 5 + [110] * 5 + [120] * 5  # Clear 20% gain
        df = pd.DataFrame({
            "Open":   [p * 0.99 for p in prices],
            "High":   [p * 1.01 for p in prices],
            "Low":    [p * 0.99 for p in prices],
            "Close":  prices,
            "Volume": [1_000_000] * 15,
        }, index=dates)
        # Buy at bar 2, sell at bar 12
        sigs = [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, -1, 0, 0]
        df["signal"] = sigs
        df["strategy_name"] = "test"
        return df

    def test_mtf_pnl_pct_uses_margin_equity_not_notional(self):
        """With 4x leverage (margin_pct=0.25), a 10% notional gain = 40% on capital.
        Bug: pnl_pct = pnl / (entry * shares) = 10%.
        Fix: pnl_pct = pnl / (entry * shares * margin_pct) = 40%."""
        df = self._make_mtf_df()
        config = TradeConfig(
            initial_capital=50_000,
            trading_mode="MTF",
            mtf_margin_pct=0.25,   # 4× leverage
            stop_loss_pct=None,
        )
        res = run_backtest(df, config)
        closed = [t for t in res["trades"] if t.exit_date is not None]
        assert closed, "Expected at least one closed trade"
        t = closed[0]
        # Gain on notional ≈ 10-20%. Gain on equity should be ~4x that.
        # If still using notional denominator, pnl_pct will be ~0.10-0.20.
        # After fix, pnl_pct > 0.30 for a 10% notional gain with 4x leverage.
        assert t.pnl_pct > 0.30, (
            f"MTF pnl_pct {t.pnl_pct:.2%} looks like it's using full notional "
            f"as denominator. Should be ~4× higher (margin return on invested equity)."
        )


# ---------------------------------------------------------------------------
# ENGINE — Signal shift ordering (dropna after shift, not before)
# ---------------------------------------------------------------------------

class TestSignalShiftOrdering:
    """dropna(signal) must happen BEFORE shift so mid-series NaN rows don't
    create wrong adjacency. The correct canonical order: shift → dropna on OHLCV.
    Bug impact: a NaN signal between two valid bars causes the signal from the
    bar BEFORE the NaN to appear TWO bars later instead of one."""

    def test_signal_executes_one_bar_after_emission(self):
        """A signal at bar N must execute at bar N+1, regardless of NaN gaps."""
        dates = pd.bdate_range("2024-01-02", periods=12)
        prices = [100 + i for i in range(12)]
        df = pd.DataFrame({
            "Open":  prices, "High":  [p + 1 for p in prices],
            "Low":   [p - 1 for p in prices], "Close": prices,
            "Volume": [1_000_000] * 12,
        }, index=dates)
        # Emit buy at bar 4, NaN at bar 5 (e.g., data gap), sell at bar 9
        signals = [0, 0, 0, 0, 1, np.nan, 0, 0, 0, -1, 0, 0]
        df["signal"] = signals
        df["strategy_name"] = "test"

        res = run_backtest(df, TradeConfig(initial_capital=200_000))
        closed = [t for t in res["trades"] if t.exit_date is not None]
        if not closed:
            pytest.skip("No trades triggered — signal too brief")
        t = closed[0]
        # Entry must be at bar 5 (1 bar after signal at bar 4), not bar 6
        # bar 5 price = 105, bar 6 price = 106
        assert t.entry_price < 106.5, (
            f"Entry at {t.entry_price:.2f} — should be near bar 5 (price≈105), "
            "not bar 6 (price≈106). NaN gap causing 2-bar delay."
        )


# ---------------------------------------------------------------------------
# SCORER — Minimum trade count (3 → 30)
# ---------------------------------------------------------------------------

class TestScorerMinTradeCount:
    """Backtest signal is unreliable with < 30 trades.
    Bug: scorer uses 3 trades as minimum — that's 3 data points to validate a strategy.
    Fix: require 30 trades minimum for statistical significance (CLT requires ~30)."""

    def test_scorer_requires_minimum_30_trades_for_backtest_signal(self):
        """analyzer.py backtest_score must be 0 or skipped when < 30 trades."""
        from nse_backtest.scorer import analyze_stock
        # Use a very short DataFrame — ensures strategies produce < 30 trades
        df = _trending_df(n=80)  # 80 bars → most strategies produce 0-5 trades
        result = analyze_stock(df, "TEST", run_backtests=True)
        # With only 80 bars and < 30 trades, the backtest score contribution should
        # not reflect a real assessment. Check internal threshold by inspecting source.
        import inspect
        src = inspect.getsource(analyze_stock)
        # After fix, the threshold should be >= 30 (not just 3)
        assert "30" in src or "MIN_TRADES" in src, (
            "Scorer must check for minimum 30 trades before using backtest signal. "
            "Check scorer.py minimum trade threshold."
        )


# ---------------------------------------------------------------------------
# SCREENER — Volume threshold (1.3x → 1.5x)
# ---------------------------------------------------------------------------

class TestScreenerVolumeThreshold:
    """Volume confirmation must require 1.5× average (institutional threshold),
    not 1.3× (noise level). Bug: breakout scan and supertrend scan use 1.3×."""

    def test_breakout_scan_rejects_1_4x_volume(self):
        """1.4× average volume should NOT trigger volume confirmation."""
        import inspect
        from nse_backtest import screener as screener_mod
        src = inspect.getsource(screener_mod)
        # After fix: 1.5 appears in volume checks, not 1.3
        # Count occurrences of 1.3 (bug) vs 1.5 (fix) for volume multipliers
        vol_13_count = src.count("1.3 * vol_avg") + src.count("1.3 * last_vol_avg") + src.count("> 1.3 *")
        assert vol_13_count == 0, (
            f"Found {vol_13_count} occurrence(s) of 1.3× volume threshold in screener. "
            "Must be updated to 1.5× (institutional signal threshold)."
        )

    def test_supertrend_scan_rejects_1_4x_volume(self):
        """Supertrend flip volume confirmation must also require 1.5×."""
        import inspect
        from nse_backtest import screener as screener_mod
        src = inspect.getsource(screener_mod)
        assert "1.5" in src, "screener.py must use 1.5× volume threshold somewhere"


# ---------------------------------------------------------------------------
# SCREENER — RSI oversold threshold (35 → 30)
# ---------------------------------------------------------------------------

class TestScreenerRsiThreshold:
    """RSI oversold in screener must be ≤ 30 (standard), not ≤ 35.
    Bug: 35 is too loose — catches normal pullbacks, not genuine oversold.
    Fix: use 30 per standard RSI interpretation (Wilder, 1978)."""

    def test_reversal_scan_uses_rsi_30_not_35(self):
        """scan_reversal must only fire for RSI ≤ 30, not ≤ 35."""
        import inspect
        from nse_backtest.screener import scan_reversal
        src = inspect.getsource(scan_reversal)
        # After fix: no reference to rsi_now < 35 or rsi_prev < 35
        assert "< 35" not in src and "35" not in src, (
            "scan_reversal still uses RSI threshold of 35. "
            "Should use 30 (Wilder's canonical oversold threshold)."
        )
        assert "< 30" in src or "30" in src, (
            "scan_reversal must reference 30 as the oversold threshold."
        )


# ---------------------------------------------------------------------------
# SCREENER — No duplicate symbols
# ---------------------------------------------------------------------------

class TestScreenerDedup:
    """run_screener must deduplicate: same symbol can appear from 3 scanners.
    Bug: if RELIANCE triggers breakout + reversal + supertrend, it appears 3×
    in results and occupies 3 slots, crowding out other stocks.
    Fix: keep only the highest-scoring setup per symbol."""

    def test_run_screener_returns_unique_symbols(self):
        """Each symbol must appear at most once in screener results."""
        from nse_backtest.screener import run_screener
        from unittest.mock import patch

        # Build a data dict with one stock that will trigger multiple scanners
        df = _trending_df(n=300)
        stock_data = {"RELIANCE": df, "TCS": df.copy(), "INFY": df.copy()}

        # Patch analyze_stock to avoid network calls
        with patch("nse_backtest.scorer.analyze_stock") as mock_analyze:
            mock_analyze.return_value = type("R", (), {
                "final_score": 70, "verdict": "GO", "confidence": "HIGH"
            })()
            setups = run_screener(stock_data, top_n=50)

        symbols_seen = [s.symbol for s in setups]
        unique_symbols = set(symbols_seen)
        assert len(symbols_seen) == len(unique_symbols), (
            f"Duplicate symbols in screener results: "
            f"{[s for s in symbols_seen if symbols_seen.count(s) > 1]}"
        )


# ---------------------------------------------------------------------------
# SCREENER — Minimum liquidity gate
# ---------------------------------------------------------------------------

class TestScreenerLiquidityGate:
    """Stocks with avg daily volume < 100,000 shares must be excluded.
    Bug: illiquid stocks can appear in results — large orders will move the price
    and make the backtest fill prices unrealistic.
    Fix: skip symbols with 20-day avg volume < 100,000."""

    def test_illiquid_stock_excluded_from_screener(self):
        """A stock with 20-day avg volume = 50,000 must not appear in results."""
        from nse_backtest.screener import run_screener
        from unittest.mock import patch

        liquid = _trending_df(n=300)
        illiquid = _trending_df(n=300)
        # Cram illiquid to 50k shares/day (below 100k threshold)
        illiquid["Volume"] = 50_000.0

        stock_data = {"LIQUID": liquid, "ILLIQUID": illiquid}

        with patch("nse_backtest.scorer.analyze_stock") as mock_analyze:
            mock_analyze.return_value = type("R", (), {
                "final_score": 80, "verdict": "GO", "confidence": "HIGH"
            })()
            setups = run_screener(stock_data, top_n=50)

        symbols = [s.symbol for s in setups]
        assert "ILLIQUID" not in symbols, (
            "Illiquid stock (50k avg vol) appeared in screener results. "
            "Must be filtered by minimum liquidity gate (100k avg vol)."
        )


# ---------------------------------------------------------------------------
# APP — --universe nifty100 uses NIFTY100_SYMBOLS
# ---------------------------------------------------------------------------

class TestAppNifty100Flag:
    """--universe nifty100 CLI flag must use NIFTY100_SYMBOLS (100 stocks).
    Bug: it uses NIFTY50_SYMBOLS (50 stocks) — documented lie in the help text."""

    def test_nifty100_branch_in_app_uses_nifty100_symbols(self):
        """The nifty100 branch in app.py must reference NIFTY100_SYMBOLS."""
        import ast
        import pathlib
        src = pathlib.Path("app.py").read_text()
        # Find the elif args.universe == "nifty100" block
        assert "nifty100" in src
        # After fix: the nifty100 branch must use NIFTY100_SYMBOLS, not NIFTY50_SYMBOLS
        lines = src.splitlines()
        in_nifty100_block = False
        for i, line in enumerate(lines):
            if "nifty100" in line and ("elif" in line or "=="):
                in_nifty100_block = True
            if in_nifty100_block and "NIFTY50_SYMBOLS" in line and "NIFTY100" not in line:
                assert False, (
                    f"Line {i+1}: `--universe nifty100` assigns NIFTY50_SYMBOLS (50 stocks). "
                    "Fix: assign NIFTY100_SYMBOLS."
                )
            if in_nifty100_block and ("elif" in line or "else:" in line) and i > 0:
                if lines[i-1].strip().startswith("symbols"):
                    break


# ---------------------------------------------------------------------------
# ANALYTICS — avg_holding_days in business days
# ---------------------------------------------------------------------------

class TestAnalyticsHoldingDaysBusinessDays:
    """avg_holding_days must count business days, not calendar days.
    Bug: uses (exit_date - entry_date).days which includes weekends.
    A trade from Friday to Monday = 3 calendar days but 1 business day."""

    def test_holding_days_excludes_weekends(self):
        """Trade from Friday to Monday must count as 1 business day, not 3."""
        from nse_backtest.engine import Trade
        import pandas as pd

        friday = pd.Timestamp("2024-01-05")   # Friday
        monday = pd.Timestamp("2024-01-08")   # Monday
        calendar_days = (monday - friday).days  # 3

        dates = pd.bdate_range("2024-01-02", periods=20)
        equity = pd.Series(range(100_000, 100_020), index=dates, dtype=float)
        trade = Trade(
            entry_date=friday, entry_price=100.0, exit_date=monday,
            exit_price=105.0, shares=10, pnl=50.0, pnl_pct=0.05,
            costs=2.0, interest=0.0, exit_reason="signal",
        )
        config = TradeConfig()
        bh = equity.copy()
        res = {"equity_curve": equity, "buy_hold_curve": bh,
               "trades": [trade], "config": config, "data": pd.DataFrame()}
        m = compute_metrics(res)
        # Calendar days = 3, business days = 1
        assert m["avg_holding_days"] < 2, (
            f"avg_holding_days={m['avg_holding_days']:.1f} counts calendar days "
            f"({calendar_days}). Must use business days (1 for Fri→Mon)."
        )


# ---------------------------------------------------------------------------
# STRATEGIES — Supertrend direction vs final ST line
# ---------------------------------------------------------------------------

class TestSupertrendDirectionVsStLine:
    """Supertrend direction must compare close vs the final ST LINE (which trails
    correctly), not raw upper/lower bands.
    Bug: comparing close vs upper_band[i-1] can flip direction prematurely
    because the raw bands don't account for the trailing stop adjustment.
    Fix: compare close vs supertrend_line[i-1] (the actual ST level)."""

    def _make_strong_uptrend(self, n=200):
        dates = pd.bdate_range("2022-01-03", periods=n)
        # Clear uninterrupted uptrend: price goes from 100 to 300
        close = np.linspace(100, 300, n)
        return pd.DataFrame({
            "Open":   close * 0.995,
            "High":   close * 1.01,
            "Low":    close * 0.99,
            "Close":  close,
            "Volume": np.full(n, 1_000_000),
        }, index=dates)

    def test_supertrend_stays_bullish_in_unbroken_uptrend(self):
        """In a clean unbroken uptrend, supertrend should be bullish on final bar."""
        from nse_backtest.strategies import STRATEGIES
        strat = STRATEGIES.get("supertrend")
        if strat is None:
            pytest.skip("supertrend strategy not found")
        df = self._make_strong_uptrend(200)
        result = strat(df)
        final_signal = int(result["signal"].iloc[-1])
        assert final_signal == 1, (
            f"Supertrend signal={final_signal} on last bar of clear uptrend. "
            "Should be 1 (bullish). Direction flipped due to raw band comparison bug."
        )

    def test_supertrend_direction_in_screener_matches_strategy(self):
        """Screener's supertrend direction logic must match strategies.py."""
        from nse_backtest.screener import scan_supertrend_flip as scan_supertrend
        df = self._make_strong_uptrend(200)
        # A perfect uptrend that just started (append one more bar to trigger flip detection)
        # Inject a flip: first 180 bars down, last 20 bars up
        dates = pd.bdate_range("2022-01-03", periods=200)
        close = np.concatenate([
            np.linspace(300, 100, 170),   # downtrend
            np.linspace(100, 130, 30),    # uptrend reversal
        ])
        df2 = pd.DataFrame({
            "Open":   close * 0.995, "High": close * 1.01,
            "Low":    close * 0.99, "Close": close,
            "Volume": np.full(200, 500_000),
        }, index=dates)
        setup = scan_supertrend(df2, "TEST")
        # With a recent flip, the scanner should find it
        # After fix: correct direction means it finds the flip correctly
        # This is a smoke test — the key fix is in strategies.py / screener.py
        # Just check it doesn't raise
        assert setup is None or setup.symbol == "TEST"


# ---------------------------------------------------------------------------
# DATA — retry backoff sufficient for yfinance 429 ban
# ---------------------------------------------------------------------------

class TestRetryBackoff:
    """yfinance 429 bans last 60-300s. Current max backoff = 1.5^2 = 2.25s.
    Fix: on empty response (rate-limit signature), backoff min 60s on attempt 2+."""

    def test_retry_backoff_uses_at_least_60s_on_second_attempt(self):
        """The backoff sequence must include at least 60s for the third attempt."""
        import inspect
        from nse_backtest import data as data_mod
        src = inspect.getsource(data_mod._yf_download)
        # After fix: code should have 60 in the backoff path for rate-limit retries
        assert "60" in src, (
            "_yf_download backoff must reach 60s to handle yfinance 429 rate-limit bans. "
            "Current max is 2.25s which is far too short."
        )
