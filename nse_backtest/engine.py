"""
Backtesting Engine

Simulates trading with realistic constraints:
- Transaction costs (brokerage + STT + GST for Indian markets)
- Slippage
- Position sizing (fixed fractional)
- Stop-loss and take-profit
- Trade-by-trade logging
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TradeConfig:
    """Configuration for a backtest run."""

    initial_capital: float = 100_000  # Rs.1 lakh default
    position_pct: float = 1.0  # Fraction of capital per trade (1.0 = all-in)
    stop_loss_pct: Optional[float] = None  # e.g., 0.05 = 5% stop loss
    take_profit_pct: Optional[float] = None  # e.g., 0.15 = 15% target
    slippage_pct: float = 0.001  # 0.1% slippage assumption
    # Zerodha-specific costs (equity delivery)
    stt_sell_pct: float = 0.001  # STT on sell side only: 0.1%
    stamp_duty_pct: float = 0.00015  # Stamp duty: 0.015% on buy side
    gst_pct: float = 0.18  # GST: 18% on brokerage
    sebi_pct: float = 0.000001  # SEBI turnover fee
    zerodha_brokerage: float = 0.0  # Rs.0 for equity delivery on Zerodha


@dataclass
class Trade:
    """Record of a single trade."""

    entry_date: pd.Timestamp
    entry_price: float
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    shares: int = 0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    costs: float = 0.0
    exit_reason: str = ""


def run_backtest(
    data: pd.DataFrame,
    config: TradeConfig = TradeConfig(),
) -> dict:
    """
    Run backtest on data with 'signal' column.

    Signal interpretation:
        1  = Enter/hold long position
       -1  = Exit long / stay flat
        0  = Hold current state
    """
    df = data.copy()
    df = df.dropna(subset=["signal"])

    # CRITICAL: Shift signals by 1 day to eliminate look-ahead bias.
    # Signal computed from bar N's data should execute on bar N+1.
    # This means we see today's close, compute signal tonight, and
    # execute at next day's close (approximating next-day open + slippage).
    df["signal"] = df["signal"].shift(1).fillna(0).astype(int)

    n = len(df)
    if n == 0:
        empty = pd.Series(dtype=float)
        return {"equity_curve": empty, "buy_hold_curve": empty,
                "trades": [], "data": df, "config": config}

    equity = np.zeros(n)
    cash = config.initial_capital
    shares = 0
    position = np.zeros(n)
    trades: list[Trade] = []
    current_trade: Optional[Trade] = None
    buy_cost = 0.0  # Track buy-side cost for accurate PnL

    for i in range(n):
        signal = df["signal"].iloc[i]
        price = df["Close"].iloc[i]
        date = df.index[i]

        buy_price = price * (1 + config.slippage_pct)
        sell_price = price * (1 - config.slippage_pct)

        # --- Check stop-loss / take-profit ---
        if shares > 0 and current_trade is not None:
            entry_p = current_trade.entry_price
            sl_price = entry_p * (1 - config.stop_loss_pct) if config.stop_loss_pct else 0
            tp_price = entry_p * (1 + config.take_profit_pct) if config.take_profit_pct else float('inf')
            day_low = df["Low"].iloc[i]
            day_high = df["High"].iloc[i]

            # SL: triggers if Low touches SL level. Exit at SL price (or Low if gap-down)
            hit_sl = config.stop_loss_pct and day_low <= sl_price
            # TP: triggers if High touches TP level. Exit at TP price (or High if gap-up)
            hit_tp = config.take_profit_pct and day_high >= tp_price

            if hit_sl or hit_tp:
                if hit_sl:
                    # Exit at SL price, or at day's open if gap-down past SL
                    exit_p = max(sl_price, day_low) * (1 - config.slippage_pct)
                else:
                    # Exit at TP price, or at day's open if gap-up past TP
                    exit_p = min(tp_price, day_high) * (1 - config.slippage_pct)

                sc = _sell_cost(exit_p, shares, config)
                proceeds = shares * exit_p - sc
                cash += proceeds
                invested = entry_p * shares + buy_cost

                current_trade.exit_date = date
                current_trade.exit_price = exit_p
                current_trade.costs = buy_cost + sc
                current_trade.pnl = proceeds - invested
                current_trade.pnl_pct = current_trade.pnl / invested if invested > 0 else 0
                current_trade.exit_reason = "stop_loss" if hit_sl else "take_profit"
                trades.append(current_trade)

                shares = 0
                current_trade = None
                buy_cost = 0.0
                signal = 0

        # --- Process signals ---
        if signal == 1 and shares == 0:
            invest_amount = cash * config.position_pct
            shares = int(invest_amount / buy_price)
            if shares > 0:
                buy_cost = _buy_cost(buy_price, shares, config)
                cash -= (shares * buy_price + buy_cost)
                current_trade = Trade(
                    entry_date=date, entry_price=buy_price, shares=shares,
                )
            else:
                shares = 0

        elif signal == -1 and shares > 0:
            sc = _sell_cost(sell_price, shares, config)
            proceeds = shares * sell_price - sc
            cash += proceeds

            if current_trade is not None:
                invested = current_trade.entry_price * shares + buy_cost
                current_trade.exit_date = date
                current_trade.exit_price = sell_price
                current_trade.costs = buy_cost + sc
                current_trade.pnl = proceeds - invested
                current_trade.pnl_pct = current_trade.pnl / invested if invested > 0 else 0
                current_trade.exit_reason = "signal"
                trades.append(current_trade)

            shares = 0
            current_trade = None
            buy_cost = 0.0

        position[i] = shares
        equity[i] = cash + shares * price

    # Close open position at end (with slippage)
    if shares > 0 and current_trade is not None:
        fp = df["Close"].iloc[-1] * (1 - config.slippage_pct)
        sc = _sell_cost(fp, shares, config)
        invested = current_trade.entry_price * shares + buy_cost
        proceeds = shares * fp - sc
        cash += proceeds

        current_trade.exit_date = df.index[-1]
        current_trade.exit_price = fp
        current_trade.costs = buy_cost + sc
        current_trade.pnl = proceeds - invested
        current_trade.pnl_pct = current_trade.pnl / invested if invested > 0 else 0
        current_trade.exit_reason = "end_of_data"
        trades.append(current_trade)

        shares = 0
        # Update final equity to reflect actual cash after closing
        equity[-1] = cash

    df["position"] = position
    df["equity"] = equity

    # Buy and hold benchmark
    bh_shares = int(config.initial_capital / df["Close"].iloc[0])
    bh_remainder = config.initial_capital - bh_shares * df["Close"].iloc[0]
    df["buy_hold_equity"] = bh_shares * df["Close"] + bh_remainder

    return {
        "equity_curve": pd.Series(equity, index=df.index),
        "buy_hold_curve": df["buy_hold_equity"],
        "trades": trades,
        "data": df,
        "config": config,
    }


def _buy_cost(price: float, shares: int, config: TradeConfig) -> float:
    """Buy-side costs: stamp duty + GST on brokerage + SEBI fee. No STT on buy."""
    turnover = price * shares
    brokerage = config.zerodha_brokerage
    return (turnover * config.stamp_duty_pct
            + brokerage * config.gst_pct
            + turnover * config.sebi_pct)


def _sell_cost(price: float, shares: int, config: TradeConfig) -> float:
    """Sell-side costs: STT + GST on brokerage + SEBI fee."""
    turnover = price * shares
    brokerage = config.zerodha_brokerage
    return (turnover * config.stt_sell_pct
            + brokerage * config.gst_pct
            + turnover * config.sebi_pct)
