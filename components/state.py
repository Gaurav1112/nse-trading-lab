import copy, json, os, tempfile
import streamlit as st
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "capital": 100_000.0, "risk_pct": 2.0, "sl_pct": 7.0,
    "analyze_sym": "", "auto_analyze": False, "nav_page": 0,
    "watchlist": ["RELIANCE","TCS","COALINDIA","NTPC","SBIN"],
    "journal": [], "positions": [],
}
_USER_DATA_DIR = os.environ.get("NSE_LAB_DATA_DIR") or os.path.join(
    os.path.expanduser("~"), ".nse-trading-lab"
)
os.makedirs(_USER_DATA_DIR, exist_ok=True)
_JOURNAL_PATH = os.path.join(_USER_DATA_DIR, "trade_journal.json")
_POSITIONS_PATH = os.path.join(_USER_DATA_DIR, "positions.json")


def _migrate_legacy_files_at_repo_root() -> None:
    """One-shot migration: move state files from repo root to user data dir.

    Eliminates the 'force-push leaks trade data' risk class (Priya Nair H.20).
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for fname in ("trade_journal.json", "positions.json", "audit_log.jsonl"):
        old = os.path.join(repo_root, fname)
        new = os.path.join(_USER_DATA_DIR, fname)
        if os.path.exists(old) and not os.path.exists(new):
            try:
                os.replace(old, new)
            except OSError:
                pass


def init_session() -> None:
    for k, v in _DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = copy.deepcopy(v) if isinstance(v, (list, dict, set)) else v
    _migrate_legacy_files_at_repo_root()
    _load_journal_from_disk()
    _load_positions_from_disk()
    # One-time audit-log hash-chain backfill — runs silently on first session,
    # idempotent on subsequent sessions. Ensures verify_audit_log() never
    # fails on records written before the D2 commit introduced the chain.
    if not st.session_state.get("_audit_migrated"):
        try:
            from components.risk_governor import migrate_legacy_audit_log
            migrate_legacy_audit_log()
        except Exception:
            pass
        st.session_state["_audit_migrated"] = True


def get_capital() -> float:
    return float(st.session_state.get("capital", _DEFAULTS["capital"]))

def set_capital(v: float) -> None:
    st.session_state["capital"] = float(v)

def get_risk_pct() -> float:
    return float(st.session_state.get("risk_pct", _DEFAULTS["risk_pct"]))

def set_risk_pct(v: float) -> None:
    st.session_state["risk_pct"] = float(v)

def get_sl_pct() -> float:
    return float(st.session_state.get("sl_pct", _DEFAULTS["sl_pct"]))

def set_sl_pct(v: float) -> None:
    st.session_state["sl_pct"] = float(v)

def get_watchlist() -> list[str]:
    return list(st.session_state.get("watchlist", copy.deepcopy(_DEFAULTS["watchlist"])))

def set_watchlist(syms: list[str]) -> None:
    st.session_state["watchlist"] = list(syms)

def get_positions() -> list[dict]:
    return list(st.session_state.get("positions", []))

def add_position(p: dict) -> None:
    positions = [x for x in get_positions() if x.get("symbol") != p.get("symbol")]
    positions.append(p)
    st.session_state["positions"] = positions
    _save_positions_to_disk(positions)

def remove_position(i: int) -> None:
    positions = get_positions()
    if 0 <= i < len(positions):
        positions.pop(i)
    st.session_state["positions"] = positions
    _save_positions_to_disk(positions)

def get_journal() -> list[dict]:
    return list(st.session_state.get("journal", []))


def close_position(index: int, sell_price: float, sell_date: str | None = None,
                   exit_reason: str = "", lesson: str = "") -> bool:
    """Atomically move a position from positions[] → journal[] with the
    realised exit price and computed P&L. The pre-mortem/post-mortem flow
    (Track Record, Tear Sheet, Trade Replay, Discipline) depends on this.

    Returns True on success, False if the index is invalid.
    """
    from datetime import datetime as _dt
    positions = get_positions()
    if index < 0 or index >= len(positions):
        return False
    p = positions[index]
    if sell_price <= 0:
        return False
    sell_date = sell_date or _dt.now().strftime("%Y-%m-%d")
    buy_price = float(p.get("buy_price", 0) or 0)
    qty = int(p.get("qty", 0) or 0)
    gross = (sell_price - buy_price) * qty
    # Charges via the existing Zerodha cost model
    try:
        from components.tax_export import compute_charges
        charges = compute_charges(buy_price, sell_price, qty)
        net = gross - charges["total"]
        charges_breakdown = charges
    except Exception:
        net = gross
        charges_breakdown = {}
    net_return_pct = (sell_price - buy_price) / buy_price * 100 if buy_price else 0.0

    closed_trade = {
        # Schema used by every consumer (track_record, trade_replay, etc.)
        "symbol": p.get("symbol", ""),
        "buy_price": buy_price,
        "sell_price": float(sell_price),
        "qty": qty,
        "entry_date": str(p.get("entry_date") or p.get("date") or ""),
        "closed_date": sell_date,
        "exit_date": sell_date,
        "exit_reason": exit_reason or "MANUAL_CLOSE",
        "pnl": round(net, 2),
        "gross_pnl": round(gross, 2),
        "net_return_pct": round(net_return_pct, 3),
        "charges_breakdown": charges_breakdown,
        # Preserve every piece of pre-trade context for honest post-mortem
        "score_at_entry": p.get("score_at_entry"),
        "win_prob_at_entry": p.get("win_prob_at_entry"),
        "rr_at_entry": p.get("rr_at_entry"),
        "tape_at_entry": p.get("tape_at_entry"),
        "thesis": p.get("thesis", ""),
        "invalidation": p.get("invalidation", ""),
        "opened_against_engine": p.get("opened_against_engine", False),
        "override_phrase_used": p.get("override_phrase_used", ""),
        "lesson": lesson,
        # Legacy "date" key (some older readers expect it)
        "date": sell_date,
        "status": "CLOSED",
    }
    # Atomic: remove from positions, append to journal
    new_positions = positions[:index] + positions[index + 1:]
    st.session_state["positions"] = new_positions
    _save_positions_to_disk(new_positions)
    journal = get_journal()
    journal.append(closed_trade)
    st.session_state["journal"] = journal
    _save_journal_to_disk(journal)
    return True


def set_positions(positions: list[dict]) -> None:
    """Replace the positions list wholesale and persist to disk.
    Used by the Cloud restore flow — Streamlit Cloud's container filesystem
    is ephemeral, so the user re-uploads their JSON backup after any
    restart and this re-hydrates both session and disk state.
    """
    if not isinstance(positions, list):
        raise ValueError("positions must be a list")
    st.session_state["positions"] = list(positions)
    _save_positions_to_disk(st.session_state["positions"])

def set_journal(journal: list[dict]) -> None:
    """Replace the journal list wholesale and persist to disk. See set_positions."""
    if not isinstance(journal, list):
        raise ValueError("journal must be a list")
    st.session_state["journal"] = list(journal)
    _save_journal_to_disk(st.session_state["journal"])

def add_trade(t: dict) -> None:
    journal = get_journal()
    journal.append(t)
    st.session_state["journal"] = journal
    _save_journal_to_disk(journal)

def get_analyze_sym() -> str:
    return str(st.session_state.get("analyze_sym", ""))

def set_analyze_sym(sym: str) -> None:
    st.session_state["analyze_sym"] = sym

def _save_positions_to_disk(positions: list[dict]) -> None:
    d = os.path.dirname(_POSITIONS_PATH) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp_positions_", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(positions, f, indent=2, default=str)
        os.replace(tmp, _POSITIONS_PATH)
    except Exception:
        try: os.remove(tmp)
        except OSError: pass


def _load_positions_from_disk() -> None:
    if st.session_state.get("positions"):
        return
    if os.path.exists(_POSITIONS_PATH):
        try:
            with open(_POSITIONS_PATH) as f:
                st.session_state["positions"] = json.load(f)
        except Exception:
            st.session_state["positions"] = []


def _save_journal_to_disk(journal: list[dict]) -> None:
    d = os.path.dirname(_JOURNAL_PATH) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp_journal_", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(journal, f, indent=2, default=str)
        os.replace(tmp, _JOURNAL_PATH)
    except Exception:
        try: os.remove(tmp)
        except OSError: pass

def _load_journal_from_disk() -> None:
    if st.session_state.get("journal"):
        return
    if os.path.exists(_JOURNAL_PATH):
        try:
            with open(_JOURNAL_PATH) as f:
                st.session_state["journal"] = json.load(f)
        except Exception:
            st.session_state["journal"] = []
