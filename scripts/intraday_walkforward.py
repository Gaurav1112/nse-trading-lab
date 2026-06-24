"""Intraday walk-forward for RSI<threshold mean-reversion strategy.

Until this script exists, the Intraday Scanner has ZERO historical
validation — it surfaces signals without any honest expectancy number.
After running this, output/walk_forward/intraday_verdict.md carries the
realized expectancy + win rate per threshold, which the Scanner page
can then surface (replacing "Historical expectancy: UNKNOWN").

Methodology:
  - 60 days of 15-min bars per Nifty 50 symbol (yfinance limit)
  - For each bar where RSI < threshold AND all safety gates pass:
      * Entry at NEXT bar's open (no look-ahead)
      * Exit at first of: stop-loss (-1%), target (+1.5%), EOD (15:15)
  - Sweep thresholds {10, 15, 20, 25, 30}
  - Report: trades, win rate, expectancy, avg win, avg loss, profit factor

The numbers this produces are the FIRST honest forward estimate of the
intraday strategy's real edge.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd

from nse_backtest.data import NIFTY50_SYMBOLS
from nse_backtest.intraday.rsi_scanner import _wilders_rsi, _batch_fetch_15m, _series_for
from nse_backtest.intraday.safety_gates import (
    reversal_candle_confirmed, volume_confirmed,
)

OUT_PATH = Path("output/walk_forward/intraday_verdict.md")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
IST = timezone(timedelta(hours=5, minutes=30))


def _simulate_intraday_trade(
    sub: pd.DataFrame, entry_idx: int,
    *, stop_pct: float = 1.0, target_pct: float = 1.5,
) -> tuple[float, str]:
    """Walk forward from `entry_idx` until exit. Returns (return_pct, reason).
    Entry is at sub.iloc[entry_idx+1]['Open'] (next bar to avoid look-ahead).
    """
    if entry_idx + 1 >= len(sub):
        return 0.0, "NO_NEXT_BAR"
    entry_price = float(sub.iloc[entry_idx + 1]["Open"])
    entry_ts = sub.index[entry_idx + 1]
    # Entry date — exit at 15:15 IST same day if not stopped/targeted earlier
    try:
        entry_date = entry_ts.tz_convert(IST).date() if entry_ts.tz else entry_ts.date()
    except Exception:
        entry_date = entry_ts.date() if hasattr(entry_ts, "date") else None

    stop = entry_price * (1 - stop_pct / 100)
    target = entry_price * (1 + target_pct / 100)
    for i in range(entry_idx + 2, len(sub)):
        bar = sub.iloc[i]
        # End of trading day → EOD exit
        try:
            bar_date = bar.name.tz_convert(IST).date() if bar.name.tz else bar.name.date()
            bar_time = bar.name.tz_convert(IST).time() if bar.name.tz else bar.name.time()
        except Exception:
            bar_date = None
            bar_time = None
        if entry_date and bar_date != entry_date:
            # Crossed day boundary — exit at the close of the last bar of entry_date
            for j in range(i - 1, entry_idx, -1):
                prev = sub.iloc[j]
                try:
                    p_date = prev.name.tz_convert(IST).date() if prev.name.tz else prev.name.date()
                except Exception:
                    p_date = None
                if p_date == entry_date:
                    exit_price = float(prev["Close"])
                    return (exit_price / entry_price - 1) * 100, "EOD"
            return 0.0, "EOD_NOTFOUND"
        # Stop-loss hit intra-bar
        if float(bar["Low"]) <= stop:
            return (stop / entry_price - 1) * 100, "STOP"
        # Target hit intra-bar
        if float(bar["High"]) >= target:
            return (target / entry_price - 1) * 100, "TARGET"
        # 15:15 IST auto-squareoff
        if bar_time and bar_time >= (datetime.strptime("15:15", "%H:%M").time()):
            exit_price = float(bar["Close"])
            return (exit_price / entry_price - 1) * 100, "SQUAREOFF"
    # Ran out of bars
    return 0.0, "END_OF_DATA"


def run(rsi_threshold: float = 15.0, rsi_period: int = 14) -> dict:
    """Simulate every bar across the universe where RSI<threshold + gates pass."""
    print(f"Fetching 60d of 15-min bars for {len(NIFTY50_SYMBOLS)} symbols…")
    df = _batch_fetch_15m(NIFTY50_SYMBOLS, period="60d")
    if df is None:
        return {"error": "fetch failed"}

    trades: list[dict] = []
    for sym in NIFTY50_SYMBOLS:
        sub = _series_for(df, sym)
        if sub is None or len(sub) < rsi_period + 5:
            continue
        # Walk through every bar (chronologically) and find signal points
        for i in range(rsi_period + 1, len(sub) - 1):
            window = sub.iloc[: i + 1]
            rsi_now = _wilders_rsi(window["Close"], period=rsi_period)
            if rsi_now is None or rsi_now >= rsi_threshold:
                continue
            # Apply gates (no sector capitulation here — single-symbol path)
            r_ok, _ = reversal_candle_confirmed(window)
            v_ok, _ = volume_confirmed(window)
            if not (r_ok and v_ok):
                continue
            # Entry at next bar's open
            ret, reason = _simulate_intraday_trade(sub, i)
            trades.append({
                "symbol": sym, "entry_idx": i,
                "entry_ts": str(sub.index[i + 1]) if i + 1 < len(sub) else "",
                "rsi": round(float(rsi_now), 2),
                "ret_pct": round(ret, 3),
                "exit_reason": reason,
            })

    if not trades:
        return {"error": "no signals fired"}

    wins = [t for t in trades if t["ret_pct"] > 0]
    losses = [t for t in trades if t["ret_pct"] <= 0]
    n = len(trades)
    wr = len(wins) / n * 100
    avg_w = sum(t["ret_pct"] for t in wins) / len(wins) if wins else 0
    avg_l = sum(t["ret_pct"] for t in losses) / len(losses) if losses else 0
    expectancy = sum(t["ret_pct"] for t in trades) / n
    pf = (sum(t["ret_pct"] for t in wins) / -sum(t["ret_pct"] for t in losses)
          if sum(t["ret_pct"] for t in losses) < 0 else float("inf"))
    return {
        "rsi_threshold": rsi_threshold, "n_trades": n,
        "win_rate_pct": round(wr, 2),
        "avg_win_pct": round(avg_w, 3),
        "avg_loss_pct": round(avg_l, 3),
        "expectancy_pct": round(expectancy, 3),
        "profit_factor": round(pf, 2) if pf != float("inf") else "∞",
        "exit_breakdown": {
            reason: sum(1 for t in trades if t["exit_reason"] == reason)
            for reason in ("STOP", "TARGET", "EOD", "SQUAREOFF", "END_OF_DATA")
        },
    }


def main():
    thresholds = [10, 15, 20, 25, 30]
    results = []
    for th in thresholds:
        print(f"\n=== Threshold RSI<{th} ===")
        r = run(rsi_threshold=th)
        if "error" in r:
            print(f"  {r['error']}")
            results.append({"rsi_threshold": th, "n_trades": 0,
                            "win_rate_pct": 0, "expectancy_pct": 0})
            continue
        print(f"  n={r['n_trades']}  win_rate={r['win_rate_pct']}%  "
              f"expectancy={r['expectancy_pct']:+.3f}%  PF={r['profit_factor']}")
        results.append(r)

    lines = [
        "# Intraday walk-forward — RSI<threshold mean-reversion (15-min bars, 60-day window)",
        "",
        f"Generated {datetime.now(tz=IST).isoformat(timespec='seconds')}",
        "",
        "Methodology: For each bar where RSI<threshold AND reversal-candle + volume "
        "gates pass, enter at NEXT bar's open. Exit at first of: -1% stop, +1.5% target, "
        "15:15 IST squareoff, or end of trading day. Reproduce via "
        "`PYTHONPATH=. python3 scripts/intraday_walkforward.py`.",
        "",
        "## Results by threshold",
        "",
        "| RSI < | Trades | Win rate | Avg win | Avg loss | Expectancy | PF |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.get("n_trades", 0) == 0:
            lines.append(f"| {r['rsi_threshold']} | 0 | — | — | — | — | — |")
            continue
        lines.append(
            f"| {r['rsi_threshold']} | {r['n_trades']} | "
            f"{r['win_rate_pct']:.1f}% | {r['avg_win_pct']:+.2f}% | "
            f"{r['avg_loss_pct']:+.2f}% | {r['expectancy_pct']:+.3f}% | "
            f"{r['profit_factor']} |"
        )
    lines.extend([
        "",
        "## How to read",
        "",
        "- **Positive expectancy + PF > 1.0** → the strategy has measurable edge AT THIS THRESHOLD",
        "  in the 60-day window. Run again next week to see if edge persists.",
        "- **Negative expectancy** → mean-reversion at this RSI level is losing money net. ",
        "  The Intraday Scanner should NOT surface BUY at this threshold for real money.",
        "- **Very few trades (<10)** → 60-day sample too small to be meaningful; widen the window",
        "  or thresholds.",
        "",
        "## Honest caveats",
        "",
        "- 60-day window is the yfinance 15-min hard limit. Real edge measurement needs 6-12 months",
        "  of intraday data → paid feed (Kite Connect ticker ~₹2K/month).",
        "- No transaction costs modeled here; subtract ~0.1% per round-trip for STT+brokerage+slippage.",
        "- Universe = current Nifty 50 (survivorship bias).",
    ])
    OUT_PATH.write_text("\n".join(lines))
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
