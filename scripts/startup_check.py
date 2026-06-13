"""One-shot startup check — run before trading session begins.

Verifies:
  - Data dir exists and is writeable
  - No secrets are committed (kite_credentials.py, .env not in HEAD)
  - Network can reach yfinance
  - Today's tape regime
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def check_data_dir() -> tuple[bool, str]:
    d = os.environ.get("NSE_LAB_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".nse-trading-lab")
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    test = p / ".write_test"
    try:
        test.write_text("ok")
        test.unlink()
        return True, f"Data dir OK: {d}"
    except Exception as e:
        return False, f"Data dir not writeable: {d} ({e})"


def check_no_secrets_committed() -> tuple[bool, str]:
    """git ls-files must not include kite_credentials.py or .env."""
    try:
        out = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
    except subprocess.CalledProcessError:
        return True, "Not a git repo — skipping secrets check"
    leaks = [f for f in out if f in ("kite_credentials.py", ".env", "positions.json", "trade_journal.json", "audit_log.jsonl")]
    if leaks:
        return False, f"⛔ SECRETS LEAKED into git: {leaks} — remove with `git rm --cached`"
    return True, "No secret files tracked by git"


def check_yfinance() -> tuple[bool, str]:
    try:
        from nse_backtest.data import fetch_nifty50
        n = fetch_nifty50(start="2024-01-01")
        if n is not None and len(n) > 0:
            return True, f"yfinance OK — Nifty 50 last bar {n.index[-1].date()}"
        return False, "yfinance returned empty"
    except Exception as e:
        return False, f"yfinance failed: {e}"


def check_tape_regime() -> tuple[bool, str]:
    try:
        from nse_backtest.data import fetch_nifty50
        from nse_backtest.tape_monitor import assess_tape
        a = assess_tape(fetch_nifty50(start="2022-01-01"))
        if a is None:
            return False, "Tape assessment returned None"
        return True, f"Tape regime: {a.regime} (Nifty {a.nifty_close:,.0f}, 60d {a.return_60d_pct:+.1f}%)"
    except Exception as e:
        return False, f"Tape check failed: {e}"


def main():
    checks = [
        ("Data directory", check_data_dir),
        ("Git secrets",    check_no_secrets_committed),
        ("yfinance fetch", check_yfinance),
        ("Tape regime",    check_tape_regime),
    ]
    all_ok = True
    print("=" * 60)
    print("NSE Trading Lab — startup check")
    print("=" * 60)
    for name, fn in checks:
        ok, msg = fn()
        status = "✓" if ok else "✗"
        print(f"  {status} {name:20s} {msg}")
        all_ok = all_ok and ok
    print("=" * 60)
    if all_ok:
        print("✅ All checks passed — safe to launch Streamlit.")
        sys.exit(0)
    else:
        print("⛔ One or more checks failed. Fix before trading.")
        sys.exit(1)


if __name__ == "__main__":
    main()
