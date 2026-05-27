import copy, json, os, tempfile
import streamlit as st
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "capital": 100_000.0, "risk_pct": 2.0, "sl_pct": 7.0,
    "analyze_sym": "", "auto_analyze": False, "nav_page": 0,
    "watchlist": ["RELIANCE","TCS","COALINDIA","NTPC","SBIN"],
    "journal": [], "positions": [],
}
_JOURNAL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trade_journal.json")


def init_session() -> None:
    for k, v in _DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = copy.deepcopy(v) if isinstance(v, (list, dict, set)) else v
    _load_journal_from_disk()


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

def remove_position(i: int) -> None:
    positions = get_positions()
    if 0 <= i < len(positions):
        positions.pop(i)
    st.session_state["positions"] = positions

def get_journal() -> list[dict]:
    return list(st.session_state.get("journal", []))

def add_trade(t: dict) -> None:
    journal = get_journal()
    journal.append(t)
    st.session_state["journal"] = journal
    _save_journal_to_disk(journal)

def get_analyze_sym() -> str:
    return str(st.session_state.get("analyze_sym", ""))

def set_analyze_sym(sym: str) -> None:
    st.session_state["analyze_sym"] = sym

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
