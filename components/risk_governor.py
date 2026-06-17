"""Portfolio-level risk governor — owned by Kavya Rao (B.6).

Three guards:
  1. Max concurrent open positions (default 5)
  2. Cooling-off period after weekly drawdown breach (default 5%)
  3. Audit log of every verdict the user sees (for post-mortem)

All guards are CONFIGURABLE via st.session_state so the user can re-tune
in pages/11_Settings.py without code change.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Audit log lives in the user data dir (default ~/.nse-trading-lab/) so it never
# leaks into the repo. Override with NSE_LAB_DATA_DIR. (Priya Nair — Phase H.20)
_USER_DATA_DIR = os.environ.get("NSE_LAB_DATA_DIR") or os.path.join(
    os.path.expanduser("~"), ".nse-trading-lab"
)
os.makedirs(_USER_DATA_DIR, exist_ok=True)
_AUDIT_LOG_PATH = Path(_USER_DATA_DIR) / "audit_log.jsonl"

DEFAULTS = {
    "max_open_positions": 5,
    "max_per_sector": 2,              # correlated-drawdown cap: max open positions per sector
    "weekly_dd_threshold_pct": 5.0,   # cooling-off triggers when last 7d realised loss > 5% of capital
    "cooling_off_days": 7,            # length of the cooling-off period after breach
    # Regime-conditioned aggregate book vol budget (Carver, Systematic Trading ch. 9).
    # Sum of (max_loss_inr / capital) across open positions must stay under the regime's cap.
    "max_aggregate_risk_pct_trending": 6.0,
    "max_aggregate_risk_pct_mixed":    3.0,
    "max_aggregate_risk_pct_hostile":  1.0,
    # Portfolio kill switch — peak-to-trough drawdown from high-water mark
    # of (capital + open unrealized + closed realized). When breached, flatten_all=True.
    "portfolio_kill_switch_pct": 8.0,
}


@dataclass
class GovernorVerdict:
    can_trade: bool
    open_positions: int
    max_open_positions: int
    weekly_pnl_pct: float
    weekly_dd_threshold_pct: float
    cooling_off_active: bool
    cooling_off_until: Optional[str]   # ISO date when cooling-off ends, or None
    reasons: list[str]
    # New (Tier 2 audit fixes):
    aggregate_risk_pct: float = 0.0     # Current sum of risk across open positions
    aggregate_risk_cap_pct: float = 6.0  # Per-regime cap that applies right now
    flatten_all: bool = False           # Portfolio kill switch fired
    flatten_reason: str = ""            # Why kill switch fired (or "")


def _setting(name: str, fallback) -> float | int:
    """Read a tunable from st.session_state with a default fallback.

    Imported lazily so this module is importable outside Streamlit (e.g., tests).
    """
    try:
        import streamlit as st
        return st.session_state.get(name, DEFAULTS.get(name, fallback))
    except Exception:
        return DEFAULTS.get(name, fallback)


def count_open_positions(positions: list[dict]) -> int:
    """An open position is any saved dict that has not been marked closed."""
    return sum(1 for p in positions if not p.get("closed_date"))


def at_position_limit(positions: list[dict]) -> bool:
    return count_open_positions(positions) >= int(_setting("max_open_positions", 5))


def weekly_pnl_pct(journal: list[dict], capital: float, days: int = 7) -> float:
    """Sum closed-trade P&L over the last `days` calendar days, as % of capital."""
    if capital <= 0:
        return 0.0
    cutoff = datetime.now() - timedelta(days=days)
    total = 0.0
    for trade in journal:
        # Accept either 'closed_date' or 'exit_date' or 'date'
        date_str = trade.get("closed_date") or trade.get("exit_date") or trade.get("date")
        if not date_str:
            continue
        try:
            d = datetime.fromisoformat(str(date_str)[:10])
        except (ValueError, TypeError):
            continue
        if d < cutoff:
            continue
        pnl = trade.get("pnl", trade.get("net_pnl", 0.0))
        try:
            total += float(pnl)
        except (TypeError, ValueError):
            continue
    return (total / capital) * 100.0


def is_cooling_off(journal: list[dict], capital: float) -> tuple[bool, Optional[str]]:
    """Cooling-off when last `cooling_off_days` realised P&L < -threshold% of capital.

    Returns (active, until_iso_date) — until_iso_date is None when inactive.
    """
    threshold = float(_setting("weekly_dd_threshold_pct", 5.0))
    window = int(_setting("cooling_off_days", 7))
    pnl_pct = weekly_pnl_pct(journal, capital, days=window)
    if pnl_pct < -threshold:
        # Cooling-off extends `window` days from now.
        until = (datetime.now() + timedelta(days=window)).date().isoformat()
        return True, until
    return False, None


def count_by_sector(positions: list[dict]) -> dict[str, int]:
    """Tally open positions by sector. Unknown symbols -> 'Unclassified'."""
    from nse_backtest.sectors import sector_of
    out: dict[str, int] = {}
    for p in positions:
        if p.get("closed_date"):
            continue
        s = sector_of(p.get("symbol", ""))
        out[s] = out.get(s, 0) + 1
    return out


def can_open_in_sector(positions: list[dict], symbol: str) -> tuple[bool, str]:
    """Return (allowed, reason). Blocks when sector exposure >= cap.

    Unclassified sector never blocks (would otherwise refuse new listings
    or non-Nifty-50 symbols indiscriminately).
    """
    from nse_backtest.sectors import sector_of
    sector = sector_of(symbol)
    if sector == "Unclassified":
        return True, f"Sector cap: {symbol} unclassified — no block"
    cap = int(_setting("max_per_sector", 2))
    held = count_by_sector(positions).get(sector, 0)
    if held >= cap:
        return False, (
            f"Sector cap reached: already holding {held} {sector} position(s) "
            f"(cap {cap}). Close one before opening another in this sector."
        )
    return True, f"Sector OK: {sector} {held}/{cap}"


def aggregate_open_risk_pct(positions: list[dict], capital: float) -> float:
    """Sum of (max_loss_inr / capital) across open positions, in percent."""
    if capital <= 0:
        return 0.0
    total = 0.0
    for p in positions:
        if p.get("closed_date"):
            continue
        buy = float(p.get("buy_price", 0))
        sl = float(p.get("stop_loss", 0))
        qty = float(p.get("qty", 0))
        if buy > 0 and sl > 0 and qty > 0 and buy > sl:
            total += (buy - sl) * qty
    return total / capital * 100


def aggregate_risk_cap_pct(regime: str | None) -> float:
    """Per-regime aggregate book risk cap. Defaults to TRENDING when unknown."""
    if regime == "HOSTILE":
        return float(_setting("max_aggregate_risk_pct_hostile", 1.0))
    if regime == "MIXED":
        return float(_setting("max_aggregate_risk_pct_mixed", 3.0))
    if regime == "TRENDING":
        return float(_setting("max_aggregate_risk_pct_trending", 6.0))
    # Unknown regime → use TRENDING cap (most permissive); caller still has
    # the regime gate's GO-downgrade as primary defense.
    return float(_setting("max_aggregate_risk_pct_trending", 6.0))


def portfolio_kill_switch(journal: list[dict], capital: float) -> tuple[bool, str]:
    """Peak-to-trough realized drawdown from HWM. Returns (fire, reason).

    HWM is computed over the cumulative realized P&L curve; we keep this
    simple (realized only) so we don't depend on live unrealized that
    flickers with quotes. Industry analog: CME pre-trade equity stop / prop
    firm drawdown rules. Default trigger at 8% from HWM.
    """
    if capital <= 0:
        return False, ""
    closed = sorted(
        [t for t in journal if t.get("closed_date") and "pnl" in t],
        key=lambda t: t.get("closed_date"),
    )
    if not closed:
        return False, ""
    cum = 0.0
    hwm = 0.0
    for t in closed:
        try:
            cum += float(t.get("pnl", 0.0))
        except (TypeError, ValueError):
            continue
        hwm = max(hwm, cum)
    drawdown_inr = hwm - cum
    dd_pct = drawdown_inr / capital * 100
    threshold = float(_setting("portfolio_kill_switch_pct", 8.0))
    if dd_pct >= threshold:
        return True, (
            f"Portfolio drawdown {dd_pct:+.2f}% from HWM ≥ kill-switch "
            f"threshold {threshold:.1f}% — flatten all positions."
        )
    return False, ""


def assess(positions: list[dict], journal: list[dict], capital: float,
           regime: str | None = None) -> GovernorVerdict:
    """Single entry point for the UI. Returns the full risk verdict.

    `regime` ("TRENDING" / "MIXED" / "HOSTILE") gates the aggregate book risk
    budget. Callers pass the live tape_monitor regime; when omitted we fall
    back to the most permissive cap (TRENDING) and rely on the per-trade
    regime gate to block individual GO verdicts.
    """
    max_open = int(_setting("max_open_positions", 5))
    threshold = float(_setting("weekly_dd_threshold_pct", 5.0))
    open_n = count_open_positions(positions)
    pnl = weekly_pnl_pct(journal, capital)
    cool, until = is_cooling_off(journal, capital)
    reasons: list[str] = []
    can_trade = True

    if open_n >= max_open:
        can_trade = False
        reasons.append(
            f"At max concurrent positions ({open_n}/{max_open}) — close one before opening another"
        )

    if cool:
        can_trade = False
        reasons.append(
            f"Cooling-off period active until {until} — last {int(_setting('cooling_off_days', 7))}d "
            f"realised P&L {pnl:+.1f}% breached the {threshold:.1f}% drawdown threshold"
        )

    # Aggregate regime vol budget — the engine's effective tail-risk control.
    agg_risk_pct = aggregate_open_risk_pct(positions, capital)
    agg_cap_pct = aggregate_risk_cap_pct(regime)
    if agg_risk_pct >= agg_cap_pct:
        can_trade = False
        reasons.append(
            f"Aggregate book risk {agg_risk_pct:.2f}% of capital ≥ {regime or 'TRENDING'} "
            f"cap {agg_cap_pct:.1f}% — close a position or reduce sizing before adding another."
        )

    # Portfolio kill switch — flatten signal independent of "can_trade".
    flatten, flatten_reason = portfolio_kill_switch(journal, capital)
    if flatten:
        # Kill switch also blocks new opens for safety
        can_trade = False
        reasons.append(flatten_reason)

    if can_trade:
        reasons.append(
            f"Risk envelope clear: {open_n}/{max_open} positions, "
            f"aggregate risk {agg_risk_pct:.2f}%/{agg_cap_pct:.1f}% ({regime or 'TRENDING'}), "
            f"{pnl:+.1f}% weekly P&L (threshold {threshold:.1f}%)"
        )

    return GovernorVerdict(
        can_trade=can_trade, open_positions=open_n, max_open_positions=max_open,
        weekly_pnl_pct=pnl, weekly_dd_threshold_pct=threshold,
        cooling_off_active=cool, cooling_off_until=until, reasons=reasons,
        aggregate_risk_pct=agg_risk_pct, aggregate_risk_cap_pct=agg_cap_pct,
        flatten_all=flatten, flatten_reason=flatten_reason,
    )


def _hash_record(record: dict) -> str:
    """Stable sha256 of a single record's canonical JSON encoding."""
    import hashlib
    payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _last_audit_hash() -> str:
    """Return the hash of the previous record in the audit log, or '' if empty.
    Used to chain each new record to its predecessor (SEBI §25 5yr tamper-evidence).
    """
    if not _AUDIT_LOG_PATH.exists():
        return ""
    try:
        with open(_AUDIT_LOG_PATH, "rb") as f:
            try:
                f.seek(-2048, 2)
            except OSError:
                f.seek(0)
            tail = f.read().decode("utf-8", errors="ignore")
        last_line = tail.strip().split("\n")[-1] if tail.strip() else ""
        if not last_line:
            return ""
        try:
            prev = json.loads(last_line)
            return prev.get("self_hash", "") or _hash_record({k: v for k, v in prev.items() if k != "self_hash"})
        except json.JSONDecodeError:
            return ""
    except OSError:
        return ""


def log_verdict(
    symbol: str, verdict: str, score: float, win_probability: float,
    tape_regime: str, engine: str = "v2",
) -> None:
    """Append one tamper-evident JSONL record per verdict the user sees.

    Each record includes prev_hash (chains to predecessor) + self_hash so an
    auditor can verify integrity by replaying sha256(record_without_self_hash)
    and checking prev_hash[i+1] == self_hash[i]. SEBI Reg §25 (Research
    Analysts Regs 2014) requires 5-year record retention with integrity —
    plain JSONL is editable in a text editor; the hash chain raises the bar.
    """
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "symbol": symbol, "verdict": verdict,
        "score": round(float(score), 2),
        "win_probability": round(float(win_probability), 2),
        "tape_regime": tape_regime, "engine": engine,
        "prev_hash": _last_audit_hash(),
    }
    record["self_hash"] = _hash_record(record)
    try:
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    except OSError:
        pass  # Audit log failure must never break the user's trading flow.


def verify_audit_log() -> tuple[bool, str]:
    """Verify the hash chain integrity of audit_log.jsonl.
    Returns (ok, message). False on the first broken link, or True when clean.
    """
    if not _AUDIT_LOG_PATH.exists():
        return True, "Audit log is empty"
    prev_hash = ""
    try:
        with open(_AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    return False, f"Line {i}: malformed JSON"
                if rec.get("prev_hash", "") != prev_hash:
                    return False, (
                        f"Line {i}: chain broken — prev_hash={rec.get('prev_hash', '')!r} "
                        f"expected {prev_hash!r}"
                    )
                self_hash = rec.pop("self_hash", "")
                computed = _hash_record(rec)
                if computed != self_hash:
                    return False, f"Line {i}: self_hash mismatch — record was tampered"
                prev_hash = self_hash
        return True, f"Audit log integrity verified across {i} records"
    except OSError as e:
        return False, f"Could not read audit log: {e}"
