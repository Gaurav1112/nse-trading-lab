"""Tear sheet — rolling Sharpe, monthly heatmap, cumulative equity curve.

QuantConnect / PyFolio analog. Reads the user's closed-trade journal and
produces an honest performance dashboard. Returns a dict the page renders;
this module is plain-Python so it's unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import sqrt
from typing import Optional

import pandas as pd


@dataclass
class TearSheet:
    n_closed: int
    expectancy_pct: float
    win_rate_pct: float
    total_return_pct: float
    max_drawdown_pct: float
    rolling_sharpe_30d: float
    rolling_sharpe_90d: float
    sharpe_lifetime: float
    monthly_returns: dict[str, float] = field(default_factory=dict)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)
    days_in_drawdown: int = 0
    notes: list[str] = field(default_factory=list)


def _ret_of(t: dict) -> Optional[float]:
    for k in ("net_return_pct", "return_pct", "pct"):
        v = t.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    buy = t.get("buy_price") or t.get("entry_price")
    sell = t.get("sell_price") or t.get("exit_price")
    if isinstance(buy, (int, float)) and isinstance(sell, (int, float)) and buy > 0:
        return (sell - buy) / buy * 100
    return None


def _date_of(t: dict) -> Optional[datetime]:
    for k in ("closed_date", "exit_date", "date", "entry_date"):
        v = t.get(k)
        if v:
            try:
                return datetime.fromisoformat(str(v)[:10])
            except (ValueError, TypeError):
                continue
    return None


def _annualised_sharpe(returns: list[float]) -> float:
    if len(returns) < 3:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = sqrt(var) if var > 0 else 0.0
    if std == 0:
        return 0.0
    return (mean / std) * sqrt(252)


def build(journal: list[dict], capital: float) -> TearSheet:
    """Build a tear sheet from the user's closed-trade journal."""
    rows = [(d, r) for d, r in
            ((_date_of(t), _ret_of(t)) for t in journal)
            if d is not None and r is not None]
    rows.sort(key=lambda x: x[0])
    if not rows:
        return TearSheet(
            n_closed=0, expectancy_pct=0.0, win_rate_pct=0.0,
            total_return_pct=0.0, max_drawdown_pct=0.0,
            rolling_sharpe_30d=0.0, rolling_sharpe_90d=0.0, sharpe_lifetime=0.0,
            notes=["No closed trades yet — your tear sheet appears here after the first close."],
        )

    returns = [r for _, r in rows]
    dates = [d for d, _ in rows]
    n = len(returns)
    wins = sum(1 for r in returns if r > 0)
    total = sum(returns)

    # Equity curve in cumulative %, marked at each trade close
    cum = 0.0
    equity = []
    peak = 0.0
    max_dd = 0.0
    last_peak_date = dates[0]
    longest_dd_days = 0
    for d, r in rows:
        cum += r
        equity.append((d.strftime("%Y-%m-%d"), round(cum, 3)))
        if cum > peak:
            peak = cum
            last_peak_date = d
        dd = peak - cum
        max_dd = max(max_dd, dd)
        days_under = (d - last_peak_date).days
        longest_dd_days = max(longest_dd_days, days_under)

    # Monthly returns
    monthly: dict[str, float] = {}
    for d, r in rows:
        key = d.strftime("%Y-%m")
        monthly[key] = round(monthly.get(key, 0.0) + r, 3)

    # Rolling Sharpe windows (30d, 90d)
    today = datetime.now()
    last_30 = [r for d, r in rows if (today - d) <= timedelta(days=30)]
    last_90 = [r for d, r in rows if (today - d) <= timedelta(days=90)]
    sharpe_30 = _annualised_sharpe(last_30)
    sharpe_90 = _annualised_sharpe(last_90)
    sharpe_life = _annualised_sharpe(returns)

    notes = []
    if n < 30:
        notes.append(
            f"Only {n} closed trades — Sharpe and CIs are noise-dominated. "
            "30+ trades is the minimum for stable inference."
        )
    if max_dd > 5:
        notes.append(f"Max drawdown from peak: {max_dd:.2f}pp — review whether your risk caps are tight enough.")
    if longest_dd_days > 30:
        notes.append(f"Longest underwater stretch: {longest_dd_days} days — a long underwater period erodes confidence.")

    return TearSheet(
        n_closed=n,
        expectancy_pct=round(total / n, 3),
        win_rate_pct=round(wins / n * 100, 2),
        total_return_pct=round(total, 3),
        max_drawdown_pct=round(max_dd, 3),
        rolling_sharpe_30d=round(sharpe_30, 2),
        rolling_sharpe_90d=round(sharpe_90, 2),
        sharpe_lifetime=round(sharpe_life, 2),
        monthly_returns=monthly,
        equity_curve=equity,
        days_in_drawdown=longest_dd_days,
        notes=notes,
    )
