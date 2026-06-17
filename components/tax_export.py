"""Indian ITR-2 / ITR-3 tax export — Schedule 112A / Schedule CG.

Indian retail traders need per-trade charges breakdown (STT, brokerage,
stamp duty, exchange charges, GST) to file Schedule CG correctly. yfinance
+ our journal don't track these — Zerodha provides a Tax P&L statement
PDF, but a CA-ready CSV is what most traders actually want.

This module computes the charges from the engine's existing cost model and
emits a CSV in Schedule 112A format (long-term equity > ₹1L threshold) +
Schedule CG (short-term) so the user can hand it to a CA.
"""
from __future__ import annotations

from datetime import datetime, date
from io import StringIO
from typing import Optional
import csv


# Zerodha delivery costs (matches nse_backtest/picker_replay.py)
_STT_SELL = 0.001
_STAMP_BUY = 0.00015
_NSE_TXN = 0.0000297
_SEBI = 0.000001
_IPFT = 0.0000001
_GST = 0.18
_BROKERAGE_FLAT = 0  # Zerodha delivery is ₹0 (was ₹20 in older plans)
_DP_PER_SELL = 15.93


def compute_charges(buy_price: float, sell_price: float, qty: int) -> dict[str, float]:
    """Return Zerodha delivery charges breakdown for a single round-trip."""
    if qty <= 0 or buy_price <= 0 or sell_price <= 0:
        return {"stt": 0, "stamp_duty": 0, "exchange_txn": 0, "sebi_fee": 0,
                "ipft": 0, "gst": 0, "brokerage": 0, "dp_charges": 0, "total": 0}
    buy_turnover = buy_price * qty
    sell_turnover = sell_price * qty
    stt = sell_turnover * _STT_SELL
    stamp = buy_turnover * _STAMP_BUY
    txn = (buy_turnover + sell_turnover) * _NSE_TXN
    sebi = (buy_turnover + sell_turnover) * _SEBI
    ipft = (buy_turnover + sell_turnover) * _IPFT
    gst = (txn + _BROKERAGE_FLAT * 2) * _GST
    brokerage = _BROKERAGE_FLAT * 2
    dp = _DP_PER_SELL  # one-time per sell
    total = stt + stamp + txn + sebi + ipft + gst + brokerage + dp
    return {
        "stt": round(stt, 2),
        "stamp_duty": round(stamp, 2),
        "exchange_txn": round(txn, 2),
        "sebi_fee": round(sebi, 2),
        "ipft": round(ipft, 2),
        "gst": round(gst, 2),
        "brokerage": round(brokerage, 2),
        "dp_charges": round(dp, 2),
        "total": round(total, 2),
    }


def _holding_period_days(entry_str: str, exit_str: str) -> Optional[int]:
    try:
        e = datetime.fromisoformat(entry_str[:10]).date()
        x = datetime.fromisoformat(exit_str[:10]).date()
        return (x - e).days
    except (ValueError, TypeError):
        return None


def to_itr_csv(journal: list[dict]) -> str:
    """Emit a CSV the user can hand to their CA for ITR-2 Schedule 112A
    (LTCG, holding >365d) + Schedule CG (STCG, holding ≤365d).

    Columns match Income Tax India e-filing utility expected fields for
    Schedule 112A / CG section A.
    """
    out = StringIO()
    w = csv.writer(out)
    w.writerow([
        "Scrip name", "ISIN (optional)", "Quantity",
        "Buy date", "Buy value (₹)", "Cost of acquisition (₹)",
        "Sell date", "Sell value (₹)", "Net consideration (₹)",
        "STT paid (₹)", "Brokerage (₹)", "Stamp duty (₹)",
        "Exchange txn (₹)", "SEBI fee (₹)", "GST (₹)", "DP charges (₹)",
        "Total charges (₹)", "Gross P&L (₹)", "Net P&L (₹)",
        "Holding period (days)", "Tax bucket",
    ])
    for t in journal:
        sym = t.get("symbol", "")
        qty = int(t.get("qty", 0) or 0)
        buy = float(t.get("buy_price", 0) or 0)
        sell = float(t.get("sell_price") or t.get("exit_price") or 0)
        entry = str(t.get("entry_date") or t.get("date") or "")
        exit_d = str(t.get("closed_date") or t.get("exit_date") or "")
        if qty == 0 or buy == 0 or sell == 0:
            continue
        buy_value = round(buy * qty, 2)
        sell_value = round(sell * qty, 2)
        charges = compute_charges(buy, sell, qty)
        gross = round(sell_value - buy_value, 2)
        net = round(gross - charges["total"], 2)
        days = _holding_period_days(entry, exit_d)
        bucket = "LTCG" if days is not None and days > 365 else "STCG"
        w.writerow([
            sym, "", qty,
            entry[:10], buy_value, buy_value,
            exit_d[:10], sell_value, round(sell_value - charges["stt"], 2),
            charges["stt"], charges["brokerage"], charges["stamp_duty"],
            charges["exchange_txn"], charges["sebi_fee"], charges["gst"], charges["dp_charges"],
            charges["total"], gross, net,
            days if days is not None else "",
            bucket,
        ])
    return out.getvalue()
