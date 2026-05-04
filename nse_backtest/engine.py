"""Backtesting engine — Indian-equity cost model and realistic fills."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from ._logging import get_logger

log = get_logger(__name__)

REQUIRED_COLS = ("Open", "High", "Low", "Close", "Volume", "signal")
VALID_MODES = ("DELIVERY", "INTRADAY", "MTF")


@dataclass
class TradeConfig:
    """Configuration for a backtest run."""

    initial_capital: float = 100_000
    position_pct: float = 1.0
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    slippage_pct: float = 0.001

    trading_mode: str = "DELIVERY"
    exchange: str = "NSE"

    stt_buy_pct: float = 0.001
    stt_sell_pct: float = 0.001
    stt_intraday_sell_pct: float = 0.00025
    stamp_duty_pct: float = 0.00015
    nse_txn_pct: float = 0.0000297
    bse_txn_pct: float = 0.0000375
    sebi_pct: float = 0.000001
    gst_pct: float = 0.18
    zerodha_brokerage: float = 0.0
    intraday_brokerage_per_order: float = 20.0
    dp_charge_per_sell: float = 15.93
    ipft_pct: float = 0.000001  # NSE/BSE Investor Protection Fund Trust (FY 2024-25)

    mtf_interest_annual: float = 0.18
    mtf_margin_pct: float = 0.25

    def __post_init__(self) -> None:
        if self.trading_mode not in VALID_MODES:
            raise ValueError(f"trading_mode must be one of {VALID_MODES}, got {self.trading_mode!r}")
        if self.exchange not in ("NSE", "BSE"):
            raise ValueError(f"exchange must be 'NSE' or 'BSE', got {self.exchange!r}")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be > 0")
        if not (0 < self.position_pct <= 1):
            raise ValueError("position_pct must be in (0, 1]")
        if self.stop_loss_pct is not None and not (0 < self.stop_loss_pct < 1):
            raise ValueError("stop_loss_pct must be in (0, 1) when set")
        if self.take_profit_pct is not None and self.take_profit_pct <= 0:
            raise ValueError("take_profit_pct must be > 0 when set")


@dataclass
class Trade:
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    shares: int = 0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    costs: float = 0.0
    interest: float = 0.0
    exit_reason: str = ""


def _validate(data: pd.DataFrame, config: TradeConfig) -> None:
    if not isinstance(data, pd.DataFrame):
        raise TypeError("data must be a pandas DataFrame")
    missing = [c for c in REQUIRED_COLS if c not in data.columns]
    if missing:
        raise ValueError(f"DataFrame missing required columns: {missing}")
    if config.trading_mode not in VALID_MODES:
        raise ValueError(f"trading_mode must be one of {VALID_MODES}")
    if config.exchange not in ("NSE", "BSE"):
        raise ValueError("exchange must be 'NSE' or 'BSE'")
    if config.initial_capital <= 0:
        raise ValueError("initial_capital must be > 0")
    if not (0 < config.position_pct <= 1.0):
        raise ValueError("position_pct must be in (0, 1]")


def _txn_pct(config: TradeConfig) -> float:
    return config.bse_txn_pct if config.exchange == "BSE" else config.nse_txn_pct


def _brokerage(turnover: float, config: TradeConfig) -> float:
    if config.trading_mode == "DELIVERY":
        return config.zerodha_brokerage
    return min(config.intraday_brokerage_per_order, turnover * 0.0003)


def _buy_cost(price: float, shares: int, config: TradeConfig) -> float:
    turnover = price * shares
    brokerage = _brokerage(turnover, config)
    txn = turnover * _txn_pct(config)
    sebi = turnover * config.sebi_pct
    ipft = turnover * config.ipft_pct
    gst = (brokerage + txn + sebi + ipft) * config.gst_pct
    stamp = turnover * config.stamp_duty_pct
    stt = turnover * config.stt_buy_pct if config.trading_mode == "DELIVERY" else 0.0
    return brokerage + txn + sebi + ipft + gst + stamp + stt


def _sell_cost(price: float, shares: int, config: TradeConfig) -> float:
    turnover = price * shares
    brokerage = _brokerage(turnover, config)
    txn = turnover * _txn_pct(config)
    sebi = turnover * config.sebi_pct
    ipft = turnover * config.ipft_pct
    gst = (brokerage + txn + sebi + ipft) * config.gst_pct
    if config.trading_mode == "INTRADAY":
        stt = turnover * config.stt_intraday_sell_pct
        dp = 0.0
    else:
        stt = turnover * config.stt_sell_pct
        dp = config.dp_charge_per_sell
    return brokerage + txn + sebi + ipft + gst + stt + dp


def _mtf_interest(entry_price: float, shares: int, days: int, config: TradeConfig) -> float:
    if config.trading_mode != "MTF" or days <= 0:
        return 0.0
    borrowed = entry_price * shares * (1.0 - config.mtf_margin_pct)
    return borrowed * config.mtf_interest_annual * days / 365.0


def run_backtest(data: pd.DataFrame, config: Optional[TradeConfig] = None) -> dict:
    """Run a single-instrument backtest. Signals from bar N execute at bar N+1's close."""
    if config is None:
        config = TradeConfig()
    _validate(data, config)

    df = data.copy()
    df = df.dropna(subset=["signal"])
    if df.empty:
        empty = pd.Series(dtype=float)
        return {"equity_curve": empty, "buy_hold_curve": empty,
                "trades": [], "data": df, "config": config}

    df["signal"] = df["signal"].shift(1).fillna(0).astype(int)

    n = len(df)
    if n == 0 or (df["Close"] <= 0).any():
        raise ValueError("Backtest frame contains non-positive Close prices")

    equity = np.zeros(n)
    cash = float(config.initial_capital)
    shares = 0
    position = np.zeros(n)
    trades: list[Trade] = []
    current_trade: Optional[Trade] = None
    buy_cost = 0.0

    for i in range(n):
        signal = int(df["signal"].iloc[i])
        price = float(df["Close"].iloc[i])
        date = df.index[i]
        day_high = float(df["High"].iloc[i])
        day_low = float(df["Low"].iloc[i])

        buy_price = price * (1 + config.slippage_pct)
        sell_price = price * (1 - config.slippage_pct)

        if shares > 0 and current_trade is not None:
            entry_p = current_trade.entry_price
            sl_price = entry_p * (1 - config.stop_loss_pct) if config.stop_loss_pct else None
            tp_price = entry_p * (1 + config.take_profit_pct) if config.take_profit_pct else None
            hit_sl = sl_price is not None and day_low <= sl_price
            hit_tp = tp_price is not None and day_high >= tp_price

            if hit_sl or hit_tp:
                if hit_sl:
                    raw = min(sl_price, day_low)
                    exit_p = raw * (1 - config.slippage_pct)
                    reason = "stop_loss"
                else:
                    raw = min(tp_price, day_high)
                    exit_p = raw * (1 - config.slippage_pct)
                    reason = "take_profit"

                sc = _sell_cost(exit_p, shares, config)
                proceeds = shares * exit_p - sc
                cash += proceeds
                days_held = max(1, (date - current_trade.entry_date).days)
                interest = _mtf_interest(entry_p, shares, days_held, config)
                cash -= interest
                if config.trading_mode == "MTF":
                    cash -= entry_p * shares * (1.0 - config.mtf_margin_pct)

                invested = entry_p * shares + buy_cost + interest
                current_trade.exit_date = date
                current_trade.exit_price = exit_p
                current_trade.costs = buy_cost + sc
                current_trade.interest = interest
                current_trade.pnl = proceeds - invested
                current_trade.pnl_pct = current_trade.pnl / invested if invested > 0 else 0
                current_trade.exit_reason = reason
                trades.append(current_trade)

                shares = 0
                current_trade = None
                buy_cost = 0.0
                signal = 0

        if signal == 1 and shares == 0:
            leverage = (1.0 / config.mtf_margin_pct) if config.trading_mode == "MTF" else 1.0
            invest_amount = cash * config.position_pct * leverage
            tentative = int(invest_amount // buy_price)
            if tentative < 1:
                position[i] = shares
                equity[i] = cash + shares * price
                continue
            tentative_cost = _buy_cost(buy_price, tentative, config)
            # Cash required: own contribution = margin_pct * notional + costs
            own_pct = config.mtf_margin_pct if config.trading_mode == "MTF" else 1.0
            while tentative > 0 and tentative * buy_price * own_pct + tentative_cost > cash:
                tentative -= 1
                tentative_cost = _buy_cost(buy_price, tentative, config)
            if tentative > 0:
                shares = tentative
                buy_cost = tentative_cost
                cash -= shares * buy_price * own_pct + buy_cost
                current_trade = Trade(entry_date=date, entry_price=buy_price, shares=shares)

        elif signal == -1 and shares > 0:
            sc = _sell_cost(sell_price, shares, config)
            proceeds = shares * sell_price - sc
            cash += proceeds
            if current_trade is not None:
                days_held = max(1, (date - current_trade.entry_date).days)
                interest = _mtf_interest(current_trade.entry_price, shares, days_held, config)
                cash -= interest
                if config.trading_mode == "MTF":
                    cash -= current_trade.entry_price * shares * (1.0 - config.mtf_margin_pct)
                invested = current_trade.entry_price * shares + buy_cost + interest
                current_trade.exit_date = date
                current_trade.exit_price = sell_price
                current_trade.costs = buy_cost + sc
                current_trade.interest = interest
                current_trade.pnl = proceeds - invested
                current_trade.pnl_pct = current_trade.pnl / invested if invested > 0 else 0
                current_trade.exit_reason = "signal"
                trades.append(current_trade)
            shares = 0
            current_trade = None
            buy_cost = 0.0

        position[i] = shares
        if shares > 0 and current_trade is not None and config.trading_mode == "MTF":
            borrowed_outstanding = current_trade.entry_price * shares * (1.0 - config.mtf_margin_pct)
            equity[i] = cash + shares * price - borrowed_outstanding
        else:
            equity[i] = cash + shares * price

    if shares > 0 and current_trade is not None:
        final_price = float(df["Close"].iloc[-1])
        fp = final_price * (1 - config.slippage_pct)
        sc = _sell_cost(fp, shares, config)
        days_held = max(1, (df.index[-1] - current_trade.entry_date).days)
        interest = _mtf_interest(current_trade.entry_price, shares, days_held, config)
        proceeds = shares * fp - sc
        cash += proceeds - interest
        if config.trading_mode == "MTF":
            cash -= current_trade.entry_price * shares * (1.0 - config.mtf_margin_pct)
        invested = current_trade.entry_price * shares + buy_cost + interest
        current_trade.exit_date = df.index[-1]
        current_trade.exit_price = fp
        current_trade.costs = buy_cost + sc
        current_trade.interest = interest
        current_trade.pnl = proceeds - invested
        current_trade.pnl_pct = current_trade.pnl / invested if invested > 0 else 0
        current_trade.exit_reason = "end_of_data"
        trades.append(current_trade)
        shares = 0
        equity[-1] = cash

    df["position"] = position
    df["equity"] = equity

    first_close = float(df["Close"].iloc[0])
    bh_entry = first_close * (1 + config.slippage_pct)
    bh_shares = int(config.initial_capital // bh_entry)
    if bh_shares > 0:
        bh_buy_cost = _buy_cost(bh_entry, bh_shares, config)
        bh_remainder = config.initial_capital - bh_shares * bh_entry - bh_buy_cost
        df["buy_hold_equity"] = bh_shares * df["Close"] + bh_remainder
    else:
        df["buy_hold_equity"] = float(config.initial_capital)

    return {
        "equity_curve": pd.Series(equity, index=df.index),
        "buy_hold_curve": df["buy_hold_equity"],
        "trades": trades,
        "data": df,
        "config": config,
    }
