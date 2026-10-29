"""append_override: the owner's 'I choose this anyway' row, scored as its own record."""
import json
from dataclasses import replace

import pytest

from nse_backtest.desk import audit
from nse_backtest.desk.markets import NSE
from nse_backtest.desk.models import Action
from nse_backtest.desk.verdict import decide
from tests.desk.test_scoreboard import OK, P, S, VNOW


def _verdict(action: Action):
    return replace(decide(P, S, None, OK, NSE, 100.0, VNOW), action=action, cost_per_share=0.37)


@pytest.mark.parametrize("action", [Action.SKIP, Action.WAIT])
def test_append_override_marks_the_row_as_the_owners_choice(tmp_path, action):
    v = _verdict(action)
    path = audit.append_override(v, tmp_path / "a.jsonl")
    (rec,) = [json.loads(x) for x in path.read_text().splitlines()]
    assert rec["action"] == "OVERRIDE" and rec["desk_action"] == action.value and rec["chosen_by"] == "owner"
    desk = json.loads(audit.append_verdict(v, tmp_path / "d.jsonl").read_text())
    same = [k for k in desk if k not in ("action", "created_at")]  # F1 B1: an override is stamped when it is made
    assert {k: rec[k] for k in same} == {k: desk[k] for k in same}
    assert rec["verdict_created_at"] == v.created_at
    assert (path.stat().st_mode & 0o777) == 0o600


def test_append_override_refuses_a_take(tmp_path):
    with pytest.raises(ValueError, match="^TAKE verdicts are logged as desk picks, not overrides$"):
        audit.append_override(_verdict(Action.TAKE), tmp_path / "a.jsonl")
    assert not (tmp_path / "a.jsonl").exists()


def test_append_override_defaults_to_the_data_dir_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    assert audit.append_override(_verdict(Action.SKIP)) == tmp_path / "desk_audit.jsonl"


# ---------------------------------------------------------------- F1 B1: the override's own timestamp

def test_override_row_is_stamped_now_in_market_time_not_with_the_verdicts_time(tmp_path):
    from datetime import datetime, timezone
    v = _verdict(Action.SKIP)  # built at VNOW (10:00 IST)
    later = datetime(2026, 10, 29, 5, 7, tzinfo=timezone.utc)  # 10:37 IST
    (rec,) = [json.loads(x) for x in audit.append_override(v, tmp_path / "a.jsonl", now=later).read_text()
              .splitlines()]
    assert rec["created_at"] == "2026-10-29T10:37:00+05:30" and rec["verdict_created_at"] == v.created_at
    (rec2,) = [json.loads(x) for x in audit.append_override(v, tmp_path / "b.jsonl").read_text().splitlines()]
    got = datetime.fromisoformat(rec2["created_at"])
    assert got.utcoffset().total_seconds() == 5.5 * 3600  # market tz
    assert abs((datetime.now(timezone.utc) - got).total_seconds()) < 60  # wall-clock now, not VNOW


def test_scoreboard_replays_an_override_from_when_it_was_made(tmp_path):
    """A breakout that ran before the owner chose is never credited to the override."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import pandas as pd

    from nse_backtest.desk import fills, session
    from nse_backtest.desk.models import Mode
    from nse_backtest.desk.scoreboard import Status as OStatus
    from tests.desk.test_scoreboard_io import Loader
    ist = ZoneInfo("Asia/Kolkata")
    plan = replace(P, mode=Mode.INTRADAY, entry_limit=fills.entry_limit(100, 98, NSE.tick),
                   last_bar="2026-10-27T09:45:00", last_close=99.2)
    built = datetime(2026, 10, 27, 9, 50, tzinfo=ist)
    v = decide(plan, S, None, OK, NSE, 99.2, built)
    assert v.action is Action.WAIT
    idx = pd.date_range("2026-10-27 09:15", "2026-10-27 15:15", freq="15min")
    bars = pd.DataFrame({"Open": 99.0, "High": 99.5, "Low": 98.5, "Close": 99.2}, index=idx)
    bars.loc[pd.Timestamp("2026-10-27 10:00"), ["Open", "High", "Low", "Close"]] = (99.5, 100.5, 99.2, 100.2)
    bars.loc[pd.Timestamp("2026-10-27 10:15"), ["Open", "High", "Low", "Close"]] = (100.2, 103.5, 100.1, 103.2)
    bars.loc[pd.Timestamp("2026-10-27 10:30"):, ["Open", "High", "Low", "Close"]] = (103.0, 103.1, 102.8, 103.0)

    def score(chosen_at):
        a = tmp_path / f"{chosen_at:%H%M}.jsonl"
        audit.append_override(v, a, now=chosen_at)
        res = session.run_scoreboard(audit_path=a, cache_path=tmp_path / f"{chosen_at:%H%M}.json",
                                     now=datetime(2026, 10, 28, 12, 0, tzinfo=ist),
                                     load_daily=Loader({}), load_intraday=Loader({"ABC": bars}))
        (o,) = res.outcomes
        return o
    early = score(built)  # stamped at the verdict's time, the override would be credited with the breakout
    assert early.status is OStatus.WON and early.candidate.record == "override"
    late = score(datetime(2026, 10, 27, 10, 40, tzinfo=ist))  # chosen after the move
    assert late.candidate.created_at == datetime(2026, 10, 27, 10, 40, tzinfo=ist)
    assert late.status is not OStatus.WON and late.realised_r is None


def test_m5_naive_now_is_market_time_not_machine_local(tmp_path, monkeypatch):
    import time as _time
    from datetime import datetime
    monkeypatch.setenv("TZ", "America/Los_Angeles")  # a machine clock far from the market's
    _time.tzset()
    v = _verdict(Action.SKIP)
    (rec,) = [json.loads(x) for x in audit.append_override(v, tmp_path / "a.jsonl", now=datetime(2026, 10, 29, 10, 37))
              .read_text().splitlines()]
    try:
        assert rec["created_at"] == "2026-10-29T10:37:00+05:30"
    finally:
        monkeypatch.undo()
        _time.tzset()
