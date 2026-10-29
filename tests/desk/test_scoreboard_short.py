"""H5c: the scoreboard replays ORB shorts with every long invariant mirrored, and long scoring is unchanged."""
import hashlib
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from nse_backtest.desk.models import Mode
from nse_backtest.desk.scoreboard import (
    Candidate, Status, load_candidates, resolve_intraday, resolve_swing, summarize,
)

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=IST)
F = (Status.WON, Status.LOST, Status.TIME)


# ---------------------------------------------------------------- long results pinned before H5c touched anything

_OUTCOME_FIELDS = ("status", "fill_price", "fill_at", "exit_price", "exit_at", "realised_r", "mtm_r", "bars_held",
                   "note")
_STATS_FIELDS = ("market", "mode", "n", "wins", "win_pct", "mean_r", "mean_pred_mc_r", "mean_pred_hist_r",
                 "mean_gap_r", "ci_low", "ci_high", "max_dd_r", "cumulative_r", "not_filled", "open", "unavailable",
                 "verdict", "pred_coverage", "record")


def _frame(rng, idx, base, spread):
    op = base + rng.uniform(-spread, spread, len(idx))
    cl = base + rng.uniform(-spread, spread, len(idx))
    hi = np.maximum(op, cl) + rng.uniform(0, 0.75 * spread, len(idx))
    lo = np.minimum(op, cl) - rng.uniform(0, 0.75 * spread, len(idx))
    return pd.DataFrame(np.column_stack([op, hi, lo, cl]), index=idx, columns=["Open", "High", "Low", "Close"])


def _long_fixture_outcomes():
    """Seeded long tickets over every branch (stop-limit, plain/resting LIMIT, trusted/untrusted/contradicting
    quotes, late quotes, both records, gaps, early/late logs, swing daily and mid-session)."""
    rng = np.random.default_rng(5150)
    outs = []
    idx = pd.date_range("2026-10-27 09:15", "2026-10-27 15:15", freq="15min")
    for n in range(1500):
        hm = [(9, 20), (9, 31), (10, 7), (11, 52), (14, 29), (14, 44), (14, 50), (15, 20)][n % 8]
        at = datetime(2026, 10, 27, *hm, tzinfo=IST)
        cp = [None, 99.5, 99.9, 100.0, 100.2, 100.5, 100.6, 101.0][int(rng.integers(8))]
        ql = [True, False, None][int(rng.integers(3))]
        qa = None if ql is not True else at + timedelta(minutes=int(rng.integers(-3, 12)))
        b = _frame(rng, idx, 100.3, float(rng.choice([0.6, 1.5, 3.0])))
        if n % 17 == 0:
            b = b.drop(b.index[int(rng.integers(len(b)))])  # a gap somewhere
        c = Candidate(at, "NSE", "ABC", Mode.INTRADAY, 100.0, 98.0, 103.0, 26, float(rng.choice([0.0, 0.1])), 10,
                      0.1, None, f"i{n}", entry_limit=100.5, record=["desk", "override"][n % 2], current_price=cp,
                      quote_live=ql, quote_as_of=qa)
        now = NOW if n % 5 else datetime(2026, 10, 27, 12, 0, tzinfo=IST)
        outs.append(resolve_intraday(c, b, flat_by=time(15, 15), now=now))
    days = pd.bdate_range("2026-11-02", periods=12)
    sidx = pd.date_range("2026-11-02 11:00", "2026-11-02 15:15", freq="15min")
    for n in range(400):
        df = _frame(rng, days, 100.0, 4.0)
        c = Candidate(datetime(2026, 10, 30, 18, 0, tzinfo=IST), "NSE", "P", Mode.SWING, 100.0, 98.0, 103.0,
                      int(rng.integers(1, 8)), 0.05, 10, None, 0.2, f"s{n}")
        outs.append(resolve_swing(c, df, fill_session=days[0].date(), now=NOW + timedelta(days=30)))
        cp = [None, 99.0, 100.0, 101.5][n % 4]
        cm = Candidate(datetime(2026, 11, 2, 11, 7, tzinfo=IST), "NSE", "P", Mode.SWING, 100.0, 98.0, 103.0,
                       int(rng.integers(1, 8)), 0.05, 10, 0.3, None, f"m{n}", current_price=cp,
                       quote_live=bool(n % 3), quote_as_of=datetime(2026, 11, 2, 11, 7, tzinfo=IST))
        b15 = _frame(rng, sidx, 100.5, float(rng.choice([0.6, 3.0])))
        outs.append(resolve_swing(cm, df, fill_session=days[0].date(), now=NOW + timedelta(days=30), bars15=b15))
    return outs


def _digest(outs) -> str:
    h = hashlib.sha256()
    for o in outs:
        h.update(repr(tuple(getattr(o, k) for k in _OUTCOME_FIELDS)).encode())
    for s in summarize(outs):
        h.update(repr(tuple(getattr(s, k) for k in _STATS_FIELDS)).encode())
    return h.hexdigest()


def test_h5c_long_scoring_is_pinned():
    outs = _long_fixture_outcomes()
    seen = {o.status for o in outs}
    assert {Status.WON, Status.LOST, Status.TIME, Status.NOT_FILLED, Status.OPEN, Status.UNAVAILABLE} <= seen
    assert _digest(outs) == "19101d5221e343c32658498f82ec2db6563d717eeea92337c1f012ff5cb722c0"


_CAND_FIELDS = ("created_at", "market", "symbol", "mode", "entry", "stop", "target_1", "max_hold_bars",
                "cost_per_share", "qty", "mc_expectancy_r", "history_expectancy_r", "key", "last_bar", "last_close",
                "entry_limit", "record", "current_price", "quote_live", "quote_as_of")


def _long_rows():
    base = {"action": "TAKE", "symbol": "ABC", "market": "NSE", "mode": "intraday", "entry": 100.0, "stop": 98.0,
            "target_1": 103.0, "qty": 10, "input_hash": "h", "created_at": "2026-10-27T10:07:00+05:30"}
    rows = []
    for i, extra in enumerate([{}, {"entry_limit": 100.5}, {"entry_limit": 100.55}, {"entry_limit": 99.0},
                               {"current_price": 100.2, "quote_live": True,
                                "quote_as_of": "2026-10-27T10:08:00+05:30"},
                               {"current_price": 99.0, "last_close": 99.0}, {"cost_per_share": 0.2},
                               {"mode": "swing", "created_at": "2026-10-26T18:00:00+05:30"},
                               {"action": "OVERRIDE", "desk_action": "WAIT"}, {"action": "OVERRIDE", "desk_action": "TAKE"},
                               {"created_at": "2026-10-27T15:16:00+05:30"}, {"stop": 101.0},
                               {"symbol": "XYZ", "created_at": "2026-10-27T10:01:00+05:30"},
                               {"created_at": "2026-10-27T11:00:00+05:30"}]):
        rows.append({**base, "input_hash": f"h{i}", **extra})
    return rows


def test_h5c_long_candidates_are_pinned():
    good, bad = load_candidates(_long_rows())
    h = hashlib.sha256()
    for c in good:
        h.update(repr(tuple(getattr(c, k) for k in _CAND_FIELDS)).encode())
    for _, why in bad:
        h.update(why.encode())
    assert (len(good), len(bad)) == (4, 4) and h.hexdigest() == "2493bcab7fe6d0ff564eb0dc19c7d6bc2f9b53bc1ea7596f6fe0cdad4e546a33"
