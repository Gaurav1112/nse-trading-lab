"""A/B replay: v1 baseline vs v2 (with rs_vs_nifty) on identical window.

Prints expectancy delta and writes a CSV per engine for downstream comparison.
"""
import os
from pathlib import Path

import pandas as pd

from nse_backtest.data import fetch_multiple, fetch_nifty50, NIFTY50_SYMBOLS
from nse_backtest.picker_replay import replay_picker

OUT_DIR = Path("output/ab_replay")
OUT_DIR.mkdir(parents=True, exist_ok=True)

START = os.getenv("AB_START", "2024-01-01")
END = os.getenv("AB_END", "2024-12-31")
STRIDE = int(os.getenv("AB_STRIDE", "5"))
MIN_SCORE = float(os.getenv("AB_MIN_SCORE", "65"))


def stride_filter(symbol_data: dict, stride: int) -> dict:
    if stride <= 1:
        return symbol_data
    return {s: df.iloc[::stride].copy() for s, df in symbol_data.items()}


def main():
    print("Fetching Nifty 50 symbols + index…")
    symbol_data = fetch_multiple(NIFTY50_SYMBOLS, start="2022-01-01")
    nifty = fetch_nifty50(start="2022-01-01")
    print(f"  → {len(symbol_data)} symbols, nifty bars: {len(nifty)}")

    sampled = stride_filter(symbol_data, STRIDE)

    print(f"\nReplaying v1 ({START} → {END})…")
    r1 = replay_picker(symbol_data=sampled, start=START, end=END,
                      min_score=MIN_SCORE, max_hold=15, engine="v1")

    print(f"Replaying v2 with rs_vs_nifty…")
    r2 = replay_picker(symbol_data=sampled, start=START, end=END,
                      min_score=MIN_SCORE, max_hold=15,
                      engine="v2", nifty_df=nifty)

    r1.to_dataframe().to_csv(OUT_DIR / f"v1_{START}_{END}.csv", index=False)
    r2.to_dataframe().to_csv(OUT_DIR / f"v2_{START}_{END}.csv", index=False)

    def fmt(r):
        return (f"trades={r.total_trades}, wr={r.win_rate*100:.1f}%, "
                f"avg_win={r.avg_win_pct:+.2f}%, avg_loss={r.avg_loss_pct:+.2f}%, "
                f"expectancy={r.expectancy_pct:+.2f}%, pf={r.profit_factor:.2f}")

    print("\n=== A/B RESULTS ===")
    print(f"v1: {fmt(r1)}")
    print(f"v2: {fmt(r2)}")
    delta_e = r2.expectancy_pct - r1.expectancy_pct
    delta_pf = r2.profit_factor - r1.profit_factor
    print(f"\nΔ expectancy: {delta_e:+.2f}% ({'v2 wins' if delta_e > 0 else 'v1 wins or tied'})")
    print(f"Δ profit factor: {delta_pf:+.2f}")

    verdict_path = OUT_DIR / "verdict.md"
    verdict = f"""# A/B Replay Verdict — Phase 2A: rs_vs_nifty

Window: {START} → {END}, stride={STRIDE}, min_score={MIN_SCORE}

| Metric | v1 (baseline) | v2 (rs_vs_nifty) | Δ |
|---|---|---|---|
| Trades | {r1.total_trades} | {r2.total_trades} | {r2.total_trades - r1.total_trades:+d} |
| Win rate | {r1.win_rate*100:.1f}% | {r2.win_rate*100:.1f}% | {(r2.win_rate - r1.win_rate)*100:+.1f}pp |
| Avg win | {r1.avg_win_pct:+.2f}% | {r2.avg_win_pct:+.2f}% | {r2.avg_win_pct - r1.avg_win_pct:+.2f}pp |
| Avg loss | {r1.avg_loss_pct:+.2f}% | {r2.avg_loss_pct:+.2f}% | {r2.avg_loss_pct - r1.avg_loss_pct:+.2f}pp |
| Expectancy | {r1.expectancy_pct:+.2f}% | {r2.expectancy_pct:+.2f}% | **{delta_e:+.2f}pp** |
| Profit factor | {r1.profit_factor:.2f} | {r2.profit_factor:.2f} | {delta_pf:+.2f} |

**Recommendation:** {'Ship rs_vs_nifty as the v2 default — positive expectancy delta.' if delta_e > 0 else 'Do NOT ship rs_vs_nifty as default — neutral or negative delta. Iterate on the boost magnitude/thresholds before re-running.'}
"""
    verdict_path.write_text(verdict)
    print(f"\nWrote {verdict_path}")


if __name__ == "__main__":
    main()
