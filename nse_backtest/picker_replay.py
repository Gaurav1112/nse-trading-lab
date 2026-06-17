"""Picker-Replay Backtest
========================
Walks history day by day, calls analyze_swing on truncated data
(no look-ahead), simulates the resulting trade plan forward, records
every outcome. The deliverable that proves whether the picker has edge.

Owner: Sandeep Kumar (E.13).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
import numpy as np

from .trading_modes import analyze_swing
from .exits import update_trail_stop, time_stop_triggered, ExitReason


# --- Zerodha delivery cost shorthand (matches engine.TradeConfig defaults) ---
_STT_SELL = 0.001
_STAMP_BUY = 0.00015
# Phase F (Daniel Foss): the old flat 0.1%/side slippage was a rough catch-all
# that double-counted spread. We now model spread explicitly (see
# spread_pct_for_df below) and keep a small residual slippage for queue/latency.
_SLIPPAGE_ONE_WAY = 0.00025
# Liquidity-bucketed bid-ask spread (Tier 2 audit). NSE 2023 microstructure
# paper (Jain/Patro): top-decile liquidity Nifty 50 names trade 3 bps median
# spread; bottom-decile trade 12-15 bps. Flat 7.5 bps was wrong on both ends.
_SPREAD_THIN_BPS = 0.0012      # 12 bps per side for bottom-decile turnover
_SPREAD_AVERAGE_BPS = 0.00075  # 7.5 bps per side for the middle of the universe
_SPREAD_THICK_BPS = 0.0003     # 3 bps per side for top-decile turnover
# ₹ thresholds — 20d median (Close × Volume). Roughly: thick > ₹500cr/day,
# thin < ₹50cr/day. Calibrated to current (2026) Nifty 50 turnover distribution.
_THICK_INR_PER_DAY = 500 * 10**7   # ₹500 crore
_THIN_INR_PER_DAY = 50 * 10**7     # ₹50 crore

# Intraday SL whipsaw slippage (T2.6). When the SL triggers intrabar (i.e.,
# the bar opened above SL but the low dipped to SL), Zerodha SL-M orders slip
# beyond the trigger. Empirical: 15-25 bps + a fraction of ATR on Nifty 50.
_INTRADAY_SL_SLIP_PCT = 0.0015       # 15 bps floor
_INTRADAY_SL_SLIP_ATR_FRAC = 0.10    # plus 0.1 × ATR


def spread_pct_for_df(df: pd.DataFrame) -> float:
    """Return the per-side spread (as a fraction) for this stock's liquidity
    bucket, computed from the last 20 bars of ₹ turnover."""
    if df is None or len(df) < 20 or "Volume" not in df.columns:
        return _SPREAD_AVERAGE_BPS
    last20 = df.tail(20)
    median_inr = float((last20["Close"] * last20["Volume"]).median())
    if median_inr >= _THICK_INR_PER_DAY:
        return _SPREAD_THICK_BPS
    if median_inr <= _THIN_INR_PER_DAY:
        return _SPREAD_THIN_BPS
    return _SPREAD_AVERAGE_BPS
_DP_PER_SELL = 15.93
_NSE_TXN = 0.0000297
_SEBI = 0.000001
_GST = 0.18
_IPFT = 0.0000001


def _delivery_costs_pct(entry: float, exit_price: float, shares: int,
                        spread_per_side: float = _SPREAD_AVERAGE_BPS) -> float:
    """Return total round-trip Zerodha delivery costs as % of (exit_price * shares).

    spread_per_side defaults to the universe-median 7.5 bps; for accurate
    backtesting, pass the per-symbol bucket spread from spread_pct_for_df().
    """
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
    spread = (buy_turnover + sell_turnover) * spread_per_side
    total = stt + stamp + txn + gst + dp + slip + spread
    return total / sell_turnover * 100


@dataclass
class TradeOutcome:
    symbol: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    bars_held: int
    gross_return_pct: float
    net_return_pct: float
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
    spread_per_side: float = _SPREAD_AVERAGE_BPS,
) -> Optional[TradeOutcome]:
    """Simulate a single trade forward through `future_data` (which starts at entry bar).

    Returns None when there's no future data to simulate against.
    """
    if len(future_data) < 2:
        return None

    sl = stop_loss
    t1_hit = False
    partial_exit_price = 0.0

    for i in range(1, min(len(future_data), max_hold + 1)):
        bar = future_data.iloc[i]
        bar_open = float(bar["Open"])
        bar_high = float(bar["High"])
        bar_low = float(bar["Low"])
        bar_volume = float(bar["Volume"]) if "Volume" in bar.index else 1_000_000.0

        # Phase F: circuit-lock heuristic. A bar with zero volume (or a flat bar
        # at near-zero volume) means no trades happened — we cannot exit. Carry
        # the position to the next bar; do not update SL or take partials.
        if bar_volume == 0 or (bar_high == bar_low and bar_volume < 100):
            continue

        # Phase F: check Open FIRST. A resting GTT order would fill at the Open
        # on a gap-through, not at the target/SL price.
        if bar_open <= sl:
            # Gapped DOWN through SL overnight: fill at Open (worse than SL).
            exit_price = bar_open
            if t1_hit:
                exit_price = partial_exit_price * 0.5 + bar_open * 0.5
            reason = ExitReason.TRAIL_STOP if t1_hit else ExitReason.STOP_LOSS_GAP
            return _build_outcome(
                symbol, entry_date, future_data.index[i], entry_price, exit_price,
                i, reason, atr, spread_per_side=spread_per_side,
            )

        if bar_open >= target_2:
            # Gapped UP through T2 overnight: fill at Open (better than T2).
            exit_price = bar_open
            if t1_hit:
                exit_price = partial_exit_price * 0.5 + bar_open * 0.5
            return _build_outcome(
                symbol, entry_date, future_data.index[i], entry_price, exit_price,
                i, ExitReason.TARGET_2_GAP, atr, spread_per_side=spread_per_side,
            )

        if bar_high >= target_2:
            exit_price = target_2
            if t1_hit:
                exit_price = partial_exit_price * 0.5 + target_2 * 0.5
            return _build_outcome(
                symbol, entry_date, future_data.index[i], entry_price, exit_price,
                i, ExitReason.TARGET_2, atr, spread_per_side=spread_per_side,
            )

        if bar_low <= sl:
            # T2.6 intraday SL whipsaw: when the bar OPEN was above SL but the
            # low dipped to trigger it, Zerodha SL-M slips beyond the trigger
            # in real life. Apply slippage = max(15 bps × sl, 0.1 × ATR).
            # Note: the overnight gap-through case (bar_open <= sl) is handled
            # earlier with bar_open as fill, which already captures that hit.
            sl_slip = max(_INTRADAY_SL_SLIP_PCT * sl, _INTRADAY_SL_SLIP_ATR_FRAC * atr)
            exit_price = max(0.01, sl - sl_slip)
            if t1_hit:
                exit_price = partial_exit_price * 0.5 + exit_price * 0.5
            reason = ExitReason.TRAIL_STOP if t1_hit else ExitReason.STOP_LOSS
            return _build_outcome(
                symbol, entry_date, future_data.index[i], entry_price, exit_price,
                i, reason, atr, spread_per_side=spread_per_side,
            )

        swing_low = float(future_data["Low"].iloc[max(0, i - 5):i + 1].min())
        new_sl, take_partial = update_trail_stop(
            entry=entry_price, current_sl=sl, t1=target_1, atr=atr,
            bar_high=bar_high, bar_low=bar_low, last_swing_low=swing_low,
            t1_hit_already=t1_hit,
        )
        if take_partial and not t1_hit:
            t1_hit = True
            partial_exit_price = target_1
        sl = new_sl

    last_close = float(future_data["Close"].iloc[min(max_hold, len(future_data) - 1)])
    exit_price = last_close
    if t1_hit:
        exit_price = partial_exit_price * 0.5 + last_close * 0.5
    reason = ExitReason.TARGET_1_PARTIAL if t1_hit else ExitReason.END_OF_REPLAY
    return _build_outcome(
        symbol, entry_date,
        future_data.index[min(max_hold, len(future_data) - 1)],
        entry_price, exit_price, min(max_hold, len(future_data) - 1), reason, atr,
        spread_per_side=spread_per_side,
    )


def _build_outcome(symbol, entry_date, exit_date, entry, exit_price, bars,
                   reason, atr, spread_per_side: float = _SPREAD_AVERAGE_BPS) -> TradeOutcome:
    gross = (exit_price / entry - 1) * 100 if entry > 0 else 0.0
    costs_pct = _delivery_costs_pct(entry, exit_price, shares=100,
                                    spread_per_side=spread_per_side)
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
    nifty_df=None,
    engine: str = "v1",
) -> BacktestReport:
    """Walk every trading day in [start, end], replay analyze_swing on truncated data."""
    _prev_engine = os.environ.get("NSE_SCORER_ENGINE")
    _prev_backtest = os.environ.get("NSE_BACKTEST_MODE")
    os.environ["NSE_SCORER_ENGINE"] = engine
    # Disables live earnings-calendar lookups so the historical replay isn't
    # contaminated by today's upcoming-earnings status (look-ahead bias).
    os.environ["NSE_BACKTEST_MODE"] = "1"
    try:
        report = BacktestReport()
        if not symbol_data:
            return report

        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        all_dates = sorted({d for df in symbol_data.values() for d in df.index
                            if start_ts <= d <= end_ts})

        open_until: dict[str, pd.Timestamp] = {}

        for d in all_dates:
            for sym, df in symbol_data.items():
                if one_position_per_symbol and open_until.get(sym, pd.Timestamp.min) >= d:
                    continue
                df_until = df.loc[:d]
                if len(df_until) < 60:
                    continue
                # Truncate nifty_df to the same point-in-time as df_until — otherwise
                # regime_gate / rs_vs_nifty evaluate against future nifty data and the
                # walk-forward becomes look-ahead biased.
                nifty_until = nifty_df.loc[:d] if nifty_df is not None else None
                try:
                    setup = analyze_swing(df_until, sym, capital, risk_pct, nifty_df=nifty_until)
                except Exception:
                    continue
                if setup.signal != "BUY" or setup.score < min_score:
                    continue

                atr_proxy = (setup.entry_price - setup.stop_loss) / 1.5
                future = df.loc[d:]
                # T2.5 liquidity bucket: bucket spread by 20d median turnover at
                # entry. df_until is point-in-time clean — no look-ahead.
                spread_per_side = spread_pct_for_df(df_until)
                outcome = simulate_trade(
                    symbol=sym, entry_date=d,
                    entry_price=setup.entry_price, stop_loss=setup.stop_loss,
                    target_1=setup.target_1, target_2=setup.target_2,
                    atr=max(atr_proxy, 0.01), future_data=future, max_hold=max_hold,
                    spread_per_side=spread_per_side,
                )
                if outcome is None:
                    continue
                outcome.score_at_entry = setup.score
                outcome.win_probability_at_entry = setup.win_probability
                outcome.reasons = setup.reasons[:5]
                report.trades.append(outcome)
                open_until[sym] = outcome.exit_date

        return report
    finally:
        if _prev_engine is None:
            os.environ.pop("NSE_SCORER_ENGINE", None)
        else:
            os.environ["NSE_SCORER_ENGINE"] = _prev_engine
        if _prev_backtest is None:
            os.environ.pop("NSE_BACKTEST_MODE", None)
        else:
            os.environ["NSE_BACKTEST_MODE"] = _prev_backtest
