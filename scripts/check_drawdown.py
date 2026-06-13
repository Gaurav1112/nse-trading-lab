"""Reads a backup JSON of {positions, journal} from the repo (uploaded by
the Cloud user via the Backup/Restore panel) and writes a one-line
verdict for the drawdown-alert workflow.

Workflow uses this exit code:
  0 — no alert needed
  1 — drawdown threshold breached; GHA opens / updates a tracker issue

The threshold is configurable via DRAWDOWN_PCT (default 3.0% of capital
over a rolling 7-day window). Capital default 100,000 INR; override via
NSE_LAB_CAPITAL_INR.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from components.pnl_tracker import snapshot

CAPITAL = float(os.environ.get("NSE_LAB_CAPITAL_INR", 100_000))
THRESHOLD_PCT = float(os.environ.get("DRAWDOWN_PCT", 3.0))
BACKUP_PATH = Path(os.environ.get("BACKUP_PATH", "output/cloud_state/latest_backup.json"))


def main():
    if not BACKUP_PATH.exists():
        print(f"::notice::No backup at {BACKUP_PATH} — drawdown check skipped (no Cloud-side state to read).")
        return 0
    try:
        payload = json.loads(BACKUP_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"::warning::Could not read backup: {e}")
        return 0

    journal = payload.get("journal", [])
    snap = snapshot(journal, capital=CAPITAL)
    dd_pct = -(snap.last_30d_pnl / CAPITAL * 100) if CAPITAL > 0 else 0.0

    print(f"n_closed={snap.n_closed} win_rate={snap.win_rate_pct}% "
          f"expectancy={snap.expectancy_pct:+.2f}% last_30d_pnl={snap.last_30d_pnl:+.0f} "
          f"drawdown_pct={dd_pct:+.2f}%")

    # We track drawdown as "negative last-30d realised P&L over capital".
    if snap.last_30d_pnl < -(THRESHOLD_PCT / 100) * CAPITAL:
        # Emit a payload for the workflow's issue body.
        body = (
            f"### Drawdown alert\n\n"
            f"Rolling-30d realized P&L: **{snap.last_30d_pnl:+,.0f} INR** "
            f"(**{snap.last_30d_pnl/CAPITAL*100:+.2f}%** of capital ₹{CAPITAL:,.0f}).\n\n"
            f"Threshold: -{THRESHOLD_PCT:.1f}% of capital.\n\n"
            f"- Closed trades: {snap.n_closed}\n"
            f"- Win rate: {snap.win_rate_pct:.1f}%\n"
            f"- Expectancy / trade: {snap.expectancy_pct:+.2f}%\n"
            f"- Rolling-30d Sharpe: {snap.rolling_30d_sharpe:+.2f}\n\n"
            f"### Recommended action\n\n"
            f"1. Stop opening new positions until you review your last 30 trades.\n"
            f"2. Compare engine verdicts vs your overrides — were any forced trades?\n"
            f"3. Check today's tape regime; if HOSTILE, paper-trade only.\n"
        )
        Path("drawdown_alert_body.md").write_text(body)
        print("ALERT: drawdown threshold breached.")
        return 1

    print("OK: drawdown within tolerance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
