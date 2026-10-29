from __future__ import annotations
import os
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from pipeline.fetch import fetch_ltp_with_fallback
from pipeline.gates import is_market_hours, staleness_gate
from pipeline.compute import compute_signal_batch
from pipeline.persist import persist_batch, write_health
from nse_backtest.data import fetch_nifty50, NIFTY50_SYMBOLS


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pipeline.run")


def main() -> int:
    signals_path = Path(os.environ.get("SIGNALS_REPO_PATH", "/tmp/signals"))
    health_path = signals_path / "state" / "pipeline_health.json"
    now = datetime.now(timezone.utc)
    errors: list[str] = []

    if not is_market_hours(now):
        log.info("Outside market hours — skipping compute, updating heartbeat only")
        write_health(health_path, "idle-outside-hours", [], now)
        persist_health_only(signals_path)
        return 0

    try:
        log.info("Fetching LTPs for %d symbols…", len(NIFTY50_SYMBOLS))
        ltps, source = fetch_ltp_with_fallback(NIFTY50_SYMBOLS)
        log.info("Got %d LTPs from source=%s", len(ltps), source)

        fresh, stale = staleness_gate(ltps, max_age_min=20, now=now)
        if stale:
            errors.append(f"stale: {stale[:5]}{'…' if len(stale)>5 else ''}")
            log.warning("Stale symbols dropped: %s", stale)

        log.info("Fetching Nifty daily bars for regime…")
        nifty_df = fetch_nifty50(start="2022-01-01")

        batch = compute_signal_batch(fresh, nifty_df, quote_source=source)
        log.info("Regime: %s · %d signals", batch.regime, len(batch.swing_signals))

        persist_batch(batch, signals_path, push=True)
        if batch.swing_signals:
            subs_path = signals_path / "state" / "push_subscriptions.json"
            if subs_path.exists():
                subs = json.loads(subs_path.read_text())
                from pipeline.push import send_push
                for sig in batch.swing_signals:
                    payload = {
                        "title": f"{sig.mode} · {sig.action} {sig.symbol}",
                        "body": f"Entry ₹{sig.entry:.2f} · SL {sig.stop_loss:.2f} · Tgt {sig.target:.2f}",
                        "tag": sig.signal_id,
                        "data": {"url": f"https://kite.zerodha.com/chart/web/ciq/NSE/{sig.symbol}/day"},
                    }
                    failed = send_push(payload, subs)
                    if failed:
                        errors.append(f"push_failed: {len(failed)} endpoints")
        write_health(health_path, "healthy" if not errors else "degraded", errors, now)
        persist_health_only(signals_path)
        return 0
    except Exception as e:
        log.exception("Pipeline crashed")
        errors.append(f"crash: {type(e).__name__}: {e}")
        write_health(health_path, "degraded", errors, now)
        persist_health_only(signals_path)
        return 1


def persist_health_only(repo_path: Path) -> None:
    """Push just the health file when compute was skipped or failed."""
    import subprocess
    subprocess.run(["git", "-C", str(repo_path), "add", "state/pipeline_health.json"], check=False)
    result = subprocess.run(["git", "-C", str(repo_path), "diff", "--cached", "--quiet"], check=False)
    if result.returncode == 0:
        return
    subprocess.check_call([
        "git", "-C", str(repo_path),
        "-c", "user.email=pipeline@nse-trading-lab", "-c", "user.name=nse-pipeline",
        "commit", "-m", "chore(pipeline): health heartbeat",
    ])
    subprocess.check_call(["git", "-C", str(repo_path), "push", "origin", "HEAD:main"])


if __name__ == "__main__":
    sys.exit(main())
