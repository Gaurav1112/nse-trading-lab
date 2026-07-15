import pandas as pd
from datetime import datetime, timezone
from unittest.mock import patch
from pipeline.fetch import LTPQuote
from pipeline.compute import compute_signal_batch, SignalBatch, Signal


def _fake_nifty_df():
    idx = pd.date_range("2024-01-01", periods=400, freq="B")
    return pd.DataFrame({"Close": range(21000, 21000 + 400)}, index=idx)


def test_compute_returns_signal_batch_with_regime():
    ltps = {"RELIANCE": LTPQuote("RELIANCE", 2450.0, datetime.now(timezone.utc), "fyers")}
    with patch("pipeline.compute.assess_tape") as mock_tape:
        mock_tape.return_value.regime = "MIXED"
        mock_tape.return_value.nifty_close = 21400.0
        mock_tape.return_value.recommendation = "selective"
        mock_tape.return_value.return_60d_pct = 3.2
        mock_tape.return_value.ema_200_slope_pct_20d = 0.15
        with patch("pipeline.compute._analyze_symbol") as mock_analyze:
            mock_analyze.return_value = Signal(
                signal_id="swing-2026-07-15-RELIANCE-1035",
                mode="SWING", action="BUY", symbol="RELIANCE",
                entry=2450.0, stop_loss=2410.0, target=2530.0,
                tape_regime="MIXED", thesis="test",
            )
            batch = compute_signal_batch(ltps, _fake_nifty_df())
    assert isinstance(batch, SignalBatch)
    assert batch.regime == "MIXED"
    assert len(batch.swing_signals) == 1
    assert batch.swing_signals[0].symbol == "RELIANCE"


def test_compute_includes_regime_conditions():
    ltps = {"RELIANCE": LTPQuote("RELIANCE", 2450.0, datetime.now(timezone.utc), "fyers")}
    with patch("pipeline.compute.assess_tape") as mock_tape:
        mock_tape.return_value.regime = "TRENDING"
        mock_tape.return_value.nifty_close = 21500.0
        mock_tape.return_value.return_60d_pct = 8.5
        mock_tape.return_value.ema_200_slope_pct_20d = 1.2
        with patch("pipeline.compute._analyze_symbol") as mock_analyze:
            mock_analyze.return_value = None
            batch = compute_signal_batch(ltps, _fake_nifty_df())
    assert batch.regime_conditions["nifty_close"] == 21500.0
    assert batch.regime_conditions["return_60d_pct"] == 8.5
    assert batch.regime_conditions["ema_200_slope_pct_20d"] == 1.2


def test_compute_handles_multiple_symbols():
    ltps = {
        "RELIANCE": LTPQuote("RELIANCE", 2450.0, datetime.now(timezone.utc), "fyers"),
        "TCS": LTPQuote("TCS", 3820.0, datetime.now(timezone.utc), "fyers"),
    }
    with patch("pipeline.compute.assess_tape") as mock_tape:
        mock_tape.return_value.regime = "MIXED"
        mock_tape.return_value.nifty_close = 21400.0
        mock_tape.return_value.return_60d_pct = 3.2
        mock_tape.return_value.ema_200_slope_pct_20d = 0.15
        with patch("pipeline.compute._analyze_symbol") as mock_analyze:
            def side_effect(symbol, ltp, tape):
                return Signal(
                    signal_id=f"swing-2026-07-15-{symbol}",
                    mode="SWING", action="BUY", symbol=symbol,
                    entry=ltp, stop_loss=ltp*0.98, target=ltp*1.03,
                    tape_regime="MIXED", thesis="test",
                )
            mock_analyze.side_effect = side_effect
            batch = compute_signal_batch(ltps, _fake_nifty_df())
    assert len(batch.swing_signals) == 2
    symbols = {s.symbol for s in batch.swing_signals}
    assert symbols == {"RELIANCE", "TCS"}


def test_compute_respects_generated_at_timestamp():
    ltps = {"RELIANCE": LTPQuote("RELIANCE", 2450.0, datetime.now(timezone.utc), "fyers")}
    before = datetime.now(timezone.utc)
    with patch("pipeline.compute.assess_tape") as mock_tape:
        mock_tape.return_value.regime = "MIXED"
        mock_tape.return_value.nifty_close = 21400.0
        mock_tape.return_value.return_60d_pct = 3.2
        mock_tape.return_value.ema_200_slope_pct_20d = 0.15
        with patch("pipeline.compute._analyze_symbol"):
            batch = compute_signal_batch(ltps, _fake_nifty_df())
    after = datetime.now(timezone.utc)
    assert before <= batch.generated_at <= after


def test_compute_stub_signal_on_non_hostile_regime():
    """Test that _analyze_symbol stub generates correct BUY signal for non-HOSTILE regimes."""
    from pipeline.compute import _analyze_symbol
    from nse_backtest.tape_monitor import TapeAssessment

    # Mock tape for MIXED regime
    tape = TapeAssessment(
        regime="MIXED",
        nifty_close=21400.0,
        return_60d_pct=3.2,
        above_50ema=True,
        above_200ema=True,
        ema_50_above_200=False,
        ema_200_slope_pct_20d=0.15,
        recommendation="selective",
        color="#FFB800",
    )

    signal = _analyze_symbol("RELIANCE", 2450.0, tape)
    assert signal is not None
    assert signal.symbol == "RELIANCE"
    assert signal.mode == "SWING"
    assert signal.action == "BUY"
    assert signal.entry == 2450.0
    assert signal.stop_loss == 2401.0  # 2450 * 0.98 = 2401.0
    assert signal.target == 2523.5  # 2450 * 1.03 = 2523.5
    assert signal.tape_regime == "MIXED"
    assert "L1 stub" in signal.thesis


def test_analyze_symbol_returns_none_on_hostile():
    """Test that _analyze_symbol returns None for HOSTILE regime."""
    from pipeline.compute import _analyze_symbol
    from nse_backtest.tape_monitor import TapeAssessment

    tape = TapeAssessment(
        regime="HOSTILE",
        nifty_close=20900.0,
        return_60d_pct=-5.0,
        above_50ema=False,
        above_200ema=False,
        ema_50_above_200=False,
        ema_200_slope_pct_20d=-1.2,
        recommendation="paper-trade only",
        color="#FF4D4D",
    )

    signal = _analyze_symbol("RELIANCE", 2450.0, tape)
    assert signal is None
