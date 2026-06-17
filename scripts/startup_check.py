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


def check_survivorship_bias() -> tuple[bool, str]:
    """Always-on warning: NIFTY50_SYMBOLS is the CURRENT Nifty 50 roster.
    Replaying it back to 2023 silently excludes names that got booted from
    the index (often poor performers — survivorship bias UPWARD biases
    backtests). Honest workaround would be historical index composition
    from NSE Archives or a paid feed — neither is in free-tier scope.
    """
    return True, (
        "ℹ️ Survivorship bias active: backtests use the 2026 Nifty 50 roster. "
        "Names booted from the index pre-2026 are invisible — published "
        "expectancy is biased upward by an unknown but non-zero amount. "
        "Lopez de Prado ch.11 calls this the most overlooked bias in retail "
        "quant. Fix requires historical NSE index composition (out of "
        "free-tier scope; see docs/RESIDUAL_GAPS.md)."
    )


def check_bonus_adjustment_warning() -> tuple[bool, str]:
    """Indian compliance audit: yfinance MISSES Indian bonus-issue adjustments
    for ~48hrs post ex-date (RELIANCE/INFY have done this). Warn if any Nifty
    50 symbol shows a same-day price move > 30% — that's the bonus-split
    fingerprint. This is a sanity check, not an authoritative one.
    """
    try:
        from nse_backtest.data import fetch_nse, NIFTY50_SYMBOLS
        suspects = []
        for sym in NIFTY50_SYMBOLS[:5]:  # sample 5 to keep pre-flight fast
            try:
                df = fetch_nse(sym, start="2026-01-01")
                if df is None or len(df) < 2:
                    continue
                last = float(df["Close"].iloc[-1])
                prev = float(df["Close"].iloc[-2])
                if prev > 0 and abs(last / prev - 1.0) > 0.30:
                    suspects.append(f"{sym} ({(last/prev - 1)*100:+.1f}% in 1 bar)")
            except Exception:
                continue
        if suspects:
            return False, (
                f"⚠️ Bonus/split adjustment warning: " + ", ".join(suspects) +
                " — yfinance may be lagging the corporate action. Verify against NSE bhavcopy."
            )
        return True, "No corporate-action anomalies in sampled Nifty 50 closes"
    except Exception as e:
        return True, f"Bonus check skipped: {e}"


def main():
    checks = [
        ("Data directory", check_data_dir),
        ("Git secrets",    check_no_secrets_committed),
        ("yfinance fetch", check_yfinance),
        ("Tape regime",    check_tape_regime),
        ("Bonus/split adj", check_bonus_adjustment_warning),
        ("Survivorship",   check_survivorship_bias),
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
