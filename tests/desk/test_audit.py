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
    assert {k: rec[k] for k in desk if k != "action"} == {k: desk[k] for k in desk if k != "action"}
    assert (path.stat().st_mode & 0o777) == 0o600


def test_append_override_refuses_a_take(tmp_path):
    with pytest.raises(ValueError, match="^TAKE verdicts are logged as desk picks, not overrides$"):
        audit.append_override(_verdict(Action.TAKE), tmp_path / "a.jsonl")
    assert not (tmp_path / "a.jsonl").exists()


def test_append_override_defaults_to_the_data_dir_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    assert audit.append_override(_verdict(Action.SKIP)) == tmp_path / "desk_audit.jsonl"
