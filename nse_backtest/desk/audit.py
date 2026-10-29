"""Append-only JSONL audit log of desk verdicts (owner-only file mode)."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .markets import get_profile
from .models import Action, DeskVerdict, Side


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


def _row(v: DeskVerdict) -> dict:
    p, o, s = v.plan, v.odds, v.sizing
    side = {"side": p.side.value} if p.side is Side.SHORT else {}  # long rows are unchanged
    return {**side,
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
        "quote_live": v.quote_live,  # True only when current_price is a live quote (the scoreboard trusts only that)
        "quote_as_of": v.quote_as_of,  # the quote's own time: the scoreboard tests the price against that bar
    }


def _append(rec: dict, path: Path | None) -> Path:
    path = Path(path) if path is not None else data_dir() / "desk_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)  # the mode above applies only to a new file
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return path


def append_verdict(v: DeskVerdict, path: Path | None = None) -> Path:
    return _append(_row(v), path)


def append_override(v: DeskVerdict, path: Path | None = None, *, now: datetime | None = None) -> Path:
    """The owner chose a WAIT/SKIP verdict anyway: the verdict's row with action OVERRIDE, the desk's own action
    as desk_action and chosen_by "owner". The scoreboard scores these apart from the desk's TAKEs.

    created_at is when the owner chose (``now``, default the wall clock, in the market's time zone), and the
    verdict's own time is kept as verdict_created_at: the scoreboard replays an override from created_at, so a
    move that ran before the decision is never credited to it."""
    if v.action is Action.TAKE:
        raise ValueError("TAKE verdicts are logged as desk picks, not overrides")
    from .verdict import data_broken, safety_blocked  # verdict imports this module's neighbours; keep it local
    if (why := safety_blocked(v)):
        raise ValueError(f"this short can't be taken ({'; '.join(why)}): no override")
    if (why := data_broken(v)):
        raise ValueError(f"the data is broken ({'; '.join(why)}): re-check, no override")
    tz = ZoneInfo(get_profile(v.plan.market).tz)
    if now is None:
        at = datetime.now(tz)
    else:  # a naive now is market time, never the machine's local zone
        at = now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)
    at = at.isoformat()
    return _append({**_row(v), "created_at": at, "verdict_created_at": v.created_at, "action": "OVERRIDE",
                    "desk_action": v.action.value, "chosen_by": "owner"}, path)


def append_sat_out(v: DeskVerdict, path: Path | None = None) -> Path:
    """The desk sat out a trade because of the market regime alone: the verdict's own row with action SAT_OUT (every
    other field unchanged, so the scoreboard replays it exactly like a desk TAKE, in its own record). Raises
    ValueError unless the only BLOCK is the regime and the desk would otherwise have taken it (swing long, full
    check)."""
    from .verdict import sat_out_refusal  # verdict imports this module's neighbours; keep the import local
    if (why := sat_out_refusal(v, get_profile(v.plan.market))):
        raise ValueError(f"not a sat-out: {why}")
    return _append({**_row(v), "action": "SAT_OUT"}, path)
