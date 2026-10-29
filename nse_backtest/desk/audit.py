"""Append-only JSONL audit log of desk verdicts (owner-only file mode)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .models import DeskVerdict


def data_dir() -> Path:
    """The user's data directory, created owner-only (0o700); an existing one owned by the user is
    tightened to 0o700 (best effort)."""
    d = Path(os.environ.get("NSE_LAB_DATA_DIR") or (Path.home() / ".nse-trading-lab"))
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        st = d.stat()
        if st.st_uid == os.getuid() and (st.st_mode & 0o777) != 0o700:
            os.chmod(d, 0o700)
    except (OSError, AttributeError):  # AttributeError: no getuid on this platform
        pass
    return d


def append_verdict(v: DeskVerdict, path: Path | None = None) -> Path:
    path = Path(path) if path is not None else data_dir() / "desk_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    p, o, s = v.plan, v.odds, v.sizing
    rec = {
        "created_at": v.created_at, "action": v.action.value, "symbol": p.symbol, "market": p.market,
        "mode": p.mode.value, "entry": p.entry, "stop": p.stop, "target_1": p.target_1,
        "qty": s.qty, "risk_amount": s.risk_amount,
        "mc_expectancy_r": o.mc_expectancy_r if o is not None else None,
        "blocks": [c.name for c in v.blocks()], "warns": [c.name for c in v.warns()],
        "input_hash": v.input_hash,
        "max_hold_bars": p.max_hold_bars, "cost_per_share": v.cost_per_share,
        "history_expectancy_r": o.history_expectancy_r if o is not None else None,
        "target_2": p.target_2, "last_bar": p.last_bar, "current_price": v.current_price,
        "stop_basis": p.stop_basis,
        "last_close": p.last_close,  # the plan's last daily Close: the scoreboard's price-scale check
        "entry_limit": p.entry_limit,  # intraday stop-limit's limit price (None for swing)
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)  # the mode above applies only to a new file
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return path
