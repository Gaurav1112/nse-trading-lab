"""Daily P&L tracker + rolling Sharpe — what the user is actually doing.

The walk-forward numbers tell you what the engine could do historically.
This tracker tells you what YOU are doing now. Until your own P&L curve
matches the walk-forward expectancy across a meaningful sample
(>50 closed trades), trust your own ledger over historical claims.

Computes from the user's trade_journal entries; respects the existing
journal shape (pnl, exit_date, entry_date, symbol).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from dataclasses import dataclass
import math


@dataclass
class PnlSnapshot:
    n_closed: int
    total_pnl: float                # INR
    win_rate_pct: float
    expectancy_pct: float           # mean trade return in %
    rolling_30d_sharpe: float       # annualized; 0 if n<5 in window
    last_30d_pnl: float
    cumulative_returns_pct: list[float]   # running cumulative net% trade-by-trade
    notes: list[str]


def _trade_return_pct(t: dict) -> float | None:
    """Pull a per-trade % return from the journal record, tolerant of schema drift."""
    for k in ("net_return_pct", "return_pct", "pct"):
        v = t.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    buy = t.get("buy_price") or t.get("entry_price")
    sell = t.get("sell_price") or t.get("exit_price")
    if isinstance(buy, (int, float)) and isinstance(sell, (int, float)) and buy > 0:
        return (sell - buy) / buy * 100
    return None


def _trade_date(t: dict):
    for k in ("closed_date", "exit_date", "date", "entry_date"):
        v = t.get(k)
        if v:
            try:
                return datetime.fromisoformat(str(v)[:10])
            except (ValueError, TypeError):
                continue
    return None


def snapshot(journal: list[dict], capital: float) -> PnlSnapshot:
    closed = [t for t in journal if (_trade_date(t) is not None) and (_trade_return_pct(t) is not None)]
    closed.sort(key=lambda t: _trade_date(t))
    if not closed:
        return PnlSnapshot(0, 0.0, 0.0, 0.0, 0.0, 0.0, [], ["No closed trades yet — your own P&L curve will appear here."])

    returns_pct = [_trade_return_pct(t) for t in closed]
    pnls_inr = [t.get("pnl", 0.0) for t in closed]
    wins = sum(1 for r in returns_pct if r > 0)
    n = len(closed)

    cum = 0.0
    cum_curve = []
    for r in returns_pct:
        cum += r
        cum_curve.append(round(cum, 3))

    cutoff = datetime.now() - timedelta(days=30)
    last_30 = [r for t, r in zip(closed, returns_pct) if _trade_date(t) >= cutoff]
    last_30_pnl_inr = sum(p for t, p in zip(closed, pnls_inr) if _trade_date(t) >= cutoff)

    sharpe = 0.0
    if len(last_30) >= 5:
        mean = sum(last_30) / len(last_30)
        var = sum((r - mean) ** 2 for r in last_30) / (len(last_30) - 1)
        std = math.sqrt(var) if var > 0 else 0.0
        # ~252 trading days per year, ~5 trades/wk → ~250 trades/yr is the cap.
        # Use n=30-day trades scaled, but conservatively assume sample-period freq.
        if std > 0:
            # Assume 1 trade ≈ 1 trading day on average for annualization heuristic
            sharpe = (mean / std) * math.sqrt(252)

    notes = []
    if n < 30:
        notes.append(f"Only {n} closed trades — sample too small for stable inference; treat numbers as preliminary.")
    if last_30_pnl_inr < -0.03 * capital:
        notes.append(f"Last 30d realized P&L {last_30_pnl_inr:+,.0f} INR is more than -3% of capital — review your discipline.")

    return PnlSnapshot(
        n_closed=n,
        total_pnl=float(sum(pnls_inr) if pnls_inr and all(isinstance(p, (int, float)) for p in pnls_inr) else 0.0),
        win_rate_pct=round(wins / n * 100, 2),
        expectancy_pct=round(sum(returns_pct) / n, 3),
        rolling_30d_sharpe=round(sharpe, 2),
        last_30d_pnl=round(last_30_pnl_inr, 2),
        cumulative_returns_pct=cum_curve,
        notes=notes,
    )
