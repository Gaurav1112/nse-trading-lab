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
_SLIPPAGE_ONE_WAY = 0.001
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
        bar_high = float(bar["High"])
        bar_low = float(bar["Low"])

        if bar_high >= target_2:
            exit_price = target_2
            if t1_hit:
                exit_price = partial_exit_price * 0.5 + target_2 * 0.5
            return _build_outcome(
                symbol, entry_date, future_data.index[i], entry_price, exit_price,
                i, ExitReason.TARGET_2, atr,
            )

        if bar_low <= sl:
            exit_price = sl
            if t1_hit:
                exit_price = partial_exit_price * 0.5 + sl * 0.5
            reason = ExitReason.TRAIL_STOP if t1_hit else ExitReason.STOP_LOSS
            return _build_outcome(
                symbol, entry_date, future_data.index[i], entry_price, exit_price,
                i, reason, atr,
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
    )


def _build_outcome(symbol, entry_date, exit_date, entry, exit_price, bars,
                   reason, atr) -> TradeOutcome:
    gross = (exit_price / entry - 1) * 100 if entry > 0 else 0.0
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
    nifty_df=None,
    engine: str = "v1",
) -> BacktestReport:
    """Walk every trading day in [start, end], replay analyze_swing on truncated data."""
    _prev_engine = os.environ.get("NSE_SCORER_ENGINE")
    os.environ["NSE_SCORER_ENGINE"] = engine
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
    finally:
        if _prev_engine is None:
            os.environ.pop("NSE_SCORER_ENGINE", None)
        else:
            os.environ["NSE_SCORER_ENGINE"] = _prev_engine
