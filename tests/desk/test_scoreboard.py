import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from nse_backtest.desk.models import Mode
from nse_backtest.desk.scoreboard import (
    Candidate, Outcome, Status, load_candidates, resolve_intraday, resolve_swing, summarize,
)

IST = ZoneInfo("Asia/Kolkata")
D0 = datetime(2026, 10, 26, 18, 0, tzinfo=IST)   # created after close Mon
FILL = date(2026, 10, 27)
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=IST)


def cand(cps=0.0, mode=Mode.SWING, created=D0, hold=3, mc=None, hist=None, sym="ABC"):
    return Candidate(created, "NSE", sym, mode, 100.0, 98.0, 103.0, hold, cps, 10, mc, hist,
                     f"h|{created.isoformat()}")


def daily(rows, start="2026-10-27"):
    idx = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close"])


def test_fill_then_target():
    df = daily([(101, 101.5, 99.5, 100.5), (100.5, 103.2, 100.1, 103)])
    o = resolve_swing(cand(), df, fill_session=FILL, now=NOW)
    assert o.status is Status.WON and o.fill_price == 100 and o.exit_price == 103
    assert o.realised_r == pytest.approx(1.5)


def test_fill_bar_touching_stop_ignores_target():
    o = resolve_swing(cand(), daily([(101, 104, 97.5, 100)]), fill_session=FILL, now=NOW)
    assert o.status is Status.LOST and o.exit_price == 98 and o.realised_r == pytest.approx(-1.0)


def test_gap_through_stop_after_fill():
    df = daily([(101, 101.5, 99.5, 100.5), (96, 99, 95, 97)])
    o = resolve_swing(cand(), df, fill_session=FILL, now=NOW)
    assert o.status is Status.LOST and o.exit_price == 96 and o.realised_r == pytest.approx(-2.0)


def test_open_below_entry_fills_at_open():
    df = daily([(99, 100.5, 98.5, 100), (100, 103.5, 99.9, 103)])
    o = resolve_swing(cand(), df, fill_session=FILL, now=NOW)
    assert o.fill_price == 99 and o.status is Status.WON and o.realised_r == pytest.approx(2.0)


def test_not_filled():
    o = resolve_swing(cand(), daily([(101, 102, 100.2, 101)]), fill_session=FILL, now=NOW)
    assert o.status is Status.NOT_FILLED and o.realised_r is None


def test_time_exit():
    df = daily([(101, 101.5, 99.5, 100.5)] + [(100.5, 102, 99.5, 101)] * 3)
    o = resolve_swing(cand(), df, fill_session=FILL, now=NOW)
    assert o.status is Status.TIME and o.exit_price == 101 and o.realised_r == pytest.approx(0.5)


def test_open_when_too_few_bars():
    df = daily([(101, 101.5, 99.5, 100.5), (100.5, 102, 99.5, 101)])
    o = resolve_swing(cand(), df, fill_session=FILL, now=NOW)
    assert o.status is Status.OPEN and o.realised_r is None and o.mtm_r == pytest.approx(0.5)


def test_missing_fill_bar():
    df = daily([(101, 102, 99, 100)], start="2026-10-28")
    assert resolve_swing(cand(), df, fill_session=FILL, now=NOW).status is Status.UNAVAILABLE
    waiting = resolve_swing(cand(), df, fill_session=date(2026, 11, 20), now=NOW)
    assert waiting.status is Status.OPEN


def test_costs_reduce_r():
    df = daily([(101, 101.5, 99.5, 100.5), (100.5, 103.2, 100.1, 103)])
    o = resolve_swing(cand(cps=0.2), df, fill_session=FILL, now=NOW)
    assert o.realised_r == pytest.approx(1.4)


def _bars(spec):
    idx = pd.to_datetime([f"2026-10-27 {t}" for t, *_ in spec])
    return pd.DataFrame([r for _, *r in spec], index=idx, columns=["Open", "High", "Low", "Close"])


def test_intraday_fill_and_target():
    c = cand(mode=Mode.INTRADAY, created=datetime(2026, 10, 27, 9, 31, tzinfo=IST))
    bars = _bars([("09:15", 99, 99.5, 98.5, 99), ("09:30", 99, 99.5, 98.9, 99.2),
                  ("09:45", 99.5, 99.9, 99, 99.5), ("10:00", 100.2, 100.6, 99.9, 100.3),
                  ("10:15", 100.3, 103.1, 100, 103), ("15:15", 50, 200, 40, 60)])
    o = resolve_intraday(c, bars, flat_by=time(15, 15), now=NOW)
    assert o.status is Status.WON and o.fill_price == pytest.approx(100.2) and o.exit_price == 103
    assert o.realised_r == pytest.approx(1.4)


def test_intraday_time_exit_ignores_bars_at_flat_by():
    c = cand(mode=Mode.INTRADAY, created=datetime(2026, 10, 27, 9, 31, tzinfo=IST))
    bars = _bars([("09:45", 99.5, 100.1, 99, 99.5), ("10:00", 99.5, 100, 99, 100.5),
                  ("15:00", 100.5, 101, 100, 101), ("15:15", 50, 200, 40, 60)])
    o = resolve_intraday(c, bars, flat_by=time(15, 15), now=NOW)
    assert o.status is Status.TIME and o.exit_price == 101 and o.realised_r == pytest.approx(0.5)


def test_intraday_not_filled_and_open():
    c = cand(mode=Mode.INTRADAY, created=datetime(2026, 10, 27, 9, 31, tzinfo=IST))
    bars = _bars([("09:45", 99, 99.9, 98.9, 99.5)])
    assert resolve_intraday(c, bars, flat_by=time(15, 15), now=NOW).status is Status.NOT_FILLED
    live = datetime(2026, 10, 27, 11, 0, tzinfo=IST)
    assert resolve_intraday(c, bars, flat_by=time(15, 15), now=live).status is Status.OPEN


def test_load_candidates():
    base = dict(action="TAKE", market="NSE", symbol="ABC", mode="swing", entry=100, stop=98, target_1=103,
                qty=10, mc_expectancy_r=0.3, input_hash="h")
    rows = [
        dict(base, created_at="2026-10-26T10:00:00+05:30"),
        dict(base, created_at="2026-10-26T09:30:00+05:30", input_hash="h0"),  # earlier same day: kept
        dict(base, action="WAIT", created_at="2026-10-26T11:00:00+05:30"),
        dict(base, action="SKIP", created_at="2026-10-26T11:00:00+05:30"),
        {k: v for k, v in dict(base, created_at="2026-10-26T12:00:00+05:30").items() if k != "entry"},
        dict(base, symbol="XYZ", mode="intraday", created_at="2026-10-26T10:00:00+05:30"),
    ]
    cands, rejects = load_candidates(rows)
    assert len(cands) == 2 and len(rejects) == 1 and "entry" in rejects[0][1]
    abc = next(c for c in cands if c.symbol == "ABC")
    assert abc.created_at.hour == 9 and abc.max_hold_bars == 15 and abc.key.startswith("h0|")
    assert abc.cost_per_share > 0 and abc.history_expectancy_r is None
    assert next(c for c in cands if c.symbol == "XYZ").max_hold_bars == 26


def _outs(rs, mc=None, market="NSE", mode=Mode.SWING):
    out = []
    for i, r in enumerate(rs):
        c = Candidate(datetime(2026, 1, 1, tzinfo=IST) + pd.Timedelta(days=i).to_pytimedelta(), market, "S", mode,
                      100, 98, 103, 15, 0, 10, mc, None, f"k{i}")
        st = Status.WON if r > 0 else Status.LOST
        out.append(Outcome(c, st, 100, None, 100, None, r, None, 1, ""))
    return out


def test_summarize_verdicts():
    s = summarize(_outs([1.0] * 10))[0]
    assert s.verdict == "Not enough trades yet (10/30) — keep paper-trading" and s.n == 10
    s = summarize(_outs([-1.0, -0.5] * 20))[0]
    assert s.verdict == "Losing — do not trade this with real money"
    s = summarize(_outs([0.30] * 40, mc=0.30))[0]
    assert s.verdict == "Matching the odds — real money at small size (≤0.5% risk)"
    rng = np.random.default_rng(1)
    s = summarize(_outs(list(0.05 + rng.normal(0, 0.02, 40)), mc=0.60))[0]
    assert s.verdict == "Worse than the desk's odds — do not size up"
    assert s.ci_low <= s.mean_r <= s.ci_high
    assert s == summarize(_outs(list(0.05 + np.random.default_rng(1).normal(0, 0.02, 40)), mc=0.60))[0]


def test_summarize_drawdown_groups_and_counts():
    outs = _outs([1.0, -2.0, -1.0, 3.0]) + _outs([0.5], market="US")
    nf = Outcome(outs[0].candidate, Status.NOT_FILLED, None, None, None, None, None, None, 0, "")
    stats = summarize(outs + [nf])
    nse = next(s for s in stats if s.market == "NSE")
    assert len(stats) == 2 and nse.n == 4 and nse.not_filled == 1
    assert nse.max_dd_r == pytest.approx(3.0) and nse.cumulative_r == (1.0, -1.0, -2.0, 1.0)
    assert nse.mean_pred_mc_r is None and nse.wins == 2 and isinstance(nse.mean_r, float)


def test_audit_writes_enriched_fields(tmp_path):
    from dataclasses import replace
    from nse_backtest.desk import audit
    from nse_backtest.desk.markets import NSE
    from nse_backtest.desk.verdict import decide
    from tests.desk.test_verdict_service import NOW as VNOW, OK, P, S
    v = replace(decide(P, S, None, OK, NSE, 100.0, VNOW), cost_per_share=0.37)
    path = audit.append_verdict(v, tmp_path / "a.jsonl")
    rec = json.loads(path.read_text())
    for k in ("max_hold_bars", "cost_per_share", "history_expectancy_r", "target_2", "last_bar",
              "current_price", "stop_basis"):
        assert k in rec
    assert rec["cost_per_share"] == 0.37 and rec["max_hold_bars"] == 15 and rec["stop_basis"] == "user"
