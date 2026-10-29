import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from dataclasses import replace

from nse_backtest.desk.fills import entry_limit
from nse_backtest.desk.markets import get_profile
from nse_backtest.desk.models import Check, Mode, Sizing, TradePlan
from nse_backtest.desk.models import Status as CheckStatus
from nse_backtest.desk.scoreboard import (
    Candidate, Outcome, Status, default_session_of, load_candidates, resolve_intraday, resolve_swing, summarize,
)

IST = ZoneInfo("Asia/Kolkata")
# a verdict to log (for the audit-row tests)
VNOW = datetime(2026, 10, 29, 10, 0, tzinfo=IST)
P = TradePlan("ABC", "NSE", Mode.SWING, 100, 98, 103, 106, 2, 15, 100, "2026-10-28T00:00:00", "user")
S = Sizing(250, "position cap", 640.0, 25_000.0, 140.0, False, "250 shares")
OK = (Check("regime", CheckStatus.PASS, ""),)
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


def _session(over, first="09:15", last="15:00", idle=(99, 99.5, 98.9, 99.2)):  # F1 r4: incl. the containing bar
    """Contiguous 15-minute bars first..last inclusive; `over` maps "HH:MM" -> (O, H, L, C) overrides."""
    rng = pd.date_range(f"2026-10-27 {first}", f"2026-10-27 {last}", freq="15min")
    return _bars([(f"{t:%H:%M}", *over.get(f"{t:%H:%M}", idle)) for t in rng])


def test_intraday_fill_and_target():
    c = cand(mode=Mode.INTRADAY, created=datetime(2026, 10, 27, 9, 31, tzinfo=IST))
    # C1: BUY stop-limit (trigger 100, limit 100.5): 09:45 trades through 100 opening at the limit -> fill at its
    # open 100.5; the 09:30 bar starts before created_at
    bars = _bars([("09:15", 99, 99.5, 98.5, 99), ("09:30", 99, 99.5, 98.9, 99.2),
                  ("09:45", 100.5, 100.9, 100.3, 100.6), ("10:00", 100.2, 100.6, 99.9, 100.3),
                  ("10:15", 100.3, 103.1, 100, 103), ("15:15", 50, 200, 40, 60)])
    o = resolve_intraday(c, bars, flat_by=time(15, 15), now=NOW)
    assert o.status is Status.WON and o.fill_price == pytest.approx(100.5) and o.exit_price == 103
    assert o.fill_at.startswith("2026-10-27 09:45") and o.realised_r == pytest.approx(1.25)


def test_intraday_time_exit_ignores_bars_at_flat_by():
    c = cand(mode=Mode.INTRADAY, created=datetime(2026, 10, 27, 9, 31, tzinfo=IST))
    bars = _session({"09:30": (99, 99.5, 98.9, 99.2), "09:45": (100.0, 100.4, 99.8, 100.2), "10:00": (100.5, 100.8, 100.1, 100.5),
                     "15:00": (100.5, 101, 100, 101), "15:15": (50, 200, 40, 60)}, last="15:15",
                    idle=(101, 101.5, 100.5, 101))
    o = resolve_intraday(c, bars, flat_by=time(15, 15), now=NOW)
    assert o.status is Status.TIME and o.exit_price == 101 and o.realised_r == pytest.approx(0.5)


def test_intraday_not_filled_and_open():
    c = cand(mode=Mode.INTRADAY, created=datetime(2026, 10, 27, 9, 31, tzinfo=IST))
    live = datetime(2026, 10, 27, 11, 0, tzinfo=IST)
    never_triggered = _session({})  # every High below the 100 trigger
    above_limit = _session({}, idle=(101, 101.5, 100.6, 101))  # C1: triggered above the 100.5 limit, never back
    for bars in (never_triggered, above_limit):
        o = resolve_intraday(c, bars, flat_by=time(15, 15), now=NOW)
        assert o.status is Status.NOT_FILLED and o.note == "not filled inside the entry window (cancel by 14:45)"
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
    v = replace(decide(P, S, None, OK, NSE, 100.0, VNOW), cost_per_share=0.37)
    path = audit.append_verdict(v, tmp_path / "a.jsonl")
    rec = json.loads(path.read_text())
    for k in ("max_hold_bars", "cost_per_share", "history_expectancy_r", "target_2", "last_bar",
              "current_price", "stop_basis"):
        assert k in rec
    assert rec["cost_per_share"] == 0.37 and rec["max_hold_bars"] == 15 and rec["stop_basis"] == "user"


# ---------------------------------------------------------------- fix round 1

def test_swing_partial_today_bar_not_used_for_time_exit():
    c = cand(hold=1)
    now = datetime(2026, 10, 28, 11, 0, tzinfo=IST)
    df = daily([(101, 101.5, 99.5, 100.5), (100.5, 102.6, 100.1, 102.5)])  # 28th is a partial bar
    o = resolve_swing(c, df, fill_session=FILL, now=now)
    assert o.status is Status.OPEN
    o = resolve_swing(c, df, fill_session=FILL, now=now, session_closed_today=True)
    assert o.status is Status.TIME and o.exit_price == 102.5


def test_swing_partial_fill_session_today_waits():
    now = datetime(2026, 10, 27, 11, 0, tzinfo=IST)
    df = daily([(101, 101.5, 100.4, 101)])
    o = resolve_swing(cand(), df, fill_session=FILL, now=now)
    assert o.status is Status.OPEN and "close" in o.note
    o = resolve_swing(cand(), df, fill_session=FILL, now=now, session_closed_today=True)
    assert o.status is Status.NOT_FILLED


def test_swing_partial_touch_today_not_used():
    now = datetime(2026, 10, 28, 11, 0, tzinfo=IST)
    df = daily([(101, 101.5, 99.5, 100.5), (100.5, 103.5, 100.1, 103)])
    assert resolve_swing(cand(), df, fill_session=FILL, now=now).status is Status.OPEN
    assert resolve_swing(cand(), df, fill_session=FILL, now=now, session_closed_today=True).status is Status.WON


def test_intraday_incomplete_session_bars_unavailable():
    c = cand(mode=Mode.INTRADAY, created=datetime(2026, 10, 27, 9, 31, tzinfo=IST))
    bars = _session({}, last="10:45")
    o = resolve_intraday(c, bars, flat_by=time(15, 15), now=NOW)
    assert o.status is Status.UNAVAILABLE and o.note == "incomplete session bars (last bar 10:45)"
    bars = _session({"09:45": (99.5, 100.1, 99, 99.5)}, last="10:45")  # filled, would time-exit
    assert resolve_intraday(c, bars, flat_by=time(15, 15), now=NOW).status is Status.UNAVAILABLE


def test_load_candidates_session_of():
    base = dict(action="TAKE", market="NSE", symbol="ABC", mode="swing", entry=100, stop=98, target_1=103,
                qty=10, input_hash="h")
    rows = [dict(base, created_at="2026-10-26T18:00:00+05:30", input_hash="a"),
            dict(base, created_at="2026-10-27T09:20:00+05:30", input_hash="b")]
    assert len(load_candidates(rows)[0]) == 2
    tue = lambda m, t: date(2026, 10, 27)  # noqa: E731
    cands, _ = load_candidates(rows, session_of=tue)
    assert len(cands) == 1 and cands[0].key.startswith("a|")
    assert default_session_of("NSE", datetime(2026, 10, 26, 22, 0, tzinfo=ZoneInfo("UTC"))) == date(2026, 10, 27)


@pytest.mark.parametrize("row", [(float("nan"), 101.5, 99.5, 100), (101, 101.5, float("nan"), 100)],
                         ids=["nan-open", "nan-low"])
def test_swing_nan_fill_bar_unavailable(row):
    assert resolve_swing(cand(), daily([row]), fill_session=FILL, now=NOW).status is Status.UNAVAILABLE


def test_swing_nan_walked_bar_unavailable():
    df = daily([(101, 101.5, 99.5, 100.5), (100.5, float("inf"), 100.1, 103)])
    o = resolve_swing(cand(), df, fill_session=FILL, now=NOW)
    assert o.status is Status.UNAVAILABLE and o.note == "bad price data"


def test_intraday_nan_unavailable():
    c = cand(mode=Mode.INTRADAY, created=datetime(2026, 10, 27, 9, 31, tzinfo=IST))
    bars = _session({"09:45": (99.5, 100.1, 99, 99.5), "10:00": (99.5, float("nan"), 99, 100.5)})
    assert resolve_intraday(c, bars, flat_by=time(15, 15), now=NOW).status is Status.UNAVAILABLE


@pytest.mark.parametrize("cps", [-0.1, float("nan"), float("inf")])
def test_load_candidates_rejects_bad_cost(cps):
    row = dict(action="TAKE", market="NSE", symbol="ABC", mode="swing", entry=100, stop=98, target_1=103,
               qty=10, input_hash="h", created_at="2026-10-26T10:00:00+05:30", cost_per_share=cps)
    cands, rejects = load_candidates([row])
    assert not cands and "cost_per_share" in rejects[0][1]


def test_load_candidates_rejects_intraday_after_flat_by():
    row = dict(action="TAKE", market="NSE", symbol="ABC", mode="intraday", entry=100, stop=98, target_1=103,
               qty=10, input_hash="h", created_at="2026-10-27T15:15:00+05:30")
    cands, rejects = load_candidates([row])
    assert not cands and rejects[0][1] == "created after flat-by"


def test_prediction_coverage_gate():
    outs = _outs([0.30] * 40, mc=0.30)
    full = summarize(outs)[0]
    assert full.pred_coverage == 1.0 and full.verdict.startswith("Matching")
    from dataclasses import replace
    half = [replace(o, candidate=replace(o.candidate, mc_expectancy_r=None)) if i % 2 else o
            for i, o in enumerate(outs)]
    s = summarize(half)[0]
    assert s.pred_coverage == 0.5
    assert s.verdict == "Inconclusive — keep paper-trading (odds missing for 20 of 40 trades)"
    none = summarize(_outs([0.30] * 40))[0]
    assert none.verdict == "Inconclusive — keep paper-trading (odds missing for 40 of 40 trades)"
    assert summarize(_outs([1.0] * 5))[0].pred_coverage == 0.0


def test_swing_open_below_stop_exits_at_open():
    o = resolve_swing(cand(), daily([(97, 99, 96, 98)]), fill_session=FILL, now=NOW)
    assert o.status is Status.LOST and o.fill_price == 97 and o.exit_price == 97
    assert o.realised_r == pytest.approx(0.0)


def test_intraday_stop_on_fill_bar_lost_at_stop():
    c = cand(mode=Mode.INTRADAY, created=datetime(2026, 10, 27, 9, 31, tzinfo=IST))
    bars = _session({"09:45": (100.2, 100.5, 97.5, 99)})  # C1: stop-limit fill at max(Open, trigger) = 100.2
    o = resolve_intraday(c, bars, flat_by=time(15, 15), now=NOW)
    assert o.fill_price == 100.2 and o.status is Status.LOST and o.exit_price == 98
    assert o.realised_r == pytest.approx(-1.1)


def test_win_pct_excludes_cost_eaten_wins():
    outs = _outs([1.0, -0.05, 0.0, -1.0])
    s = summarize(outs)[0]
    assert s.wins == 1 and s.win_pct == pytest.approx(25.0)
    # a WON-status trade whose costs exceed the reward has realised R <= 0 and is not a win
    c = cand(cps=3.5)
    o = resolve_swing(c, daily([(101, 101.5, 99.5, 100.5), (100.5, 103.2, 100.1, 103)]), fill_session=FILL, now=NOW)
    assert o.status is Status.WON and o.realised_r < 0
    assert summarize([o])[0].wins == 0


# ---- gaps in intraday bars: never a final status from incomplete data
def _ic():
    return cand(mode=Mode.INTRADAY, created=datetime(2026, 10, 27, 9, 31, tzinfo=IST))


def _without(bars, hhmm):
    return bars.drop(pd.Timestamp(f"2026-10-27 {hhmm}"))


def test_intraday_gap_hides_stop_then_target_unavailable():
    bars = _session({"10:15": (100.3, 100.6, 100, 100.3), "11:00": (97, 97.5, 96, 97),
                     "11:15": (100, 103.5, 100, 103)})
    assert resolve_intraday(_ic(), bars, flat_by=time(15, 15), now=NOW).status is Status.LOST  # complete control: stop gap at 11:00
    o = resolve_intraday(_ic(), _without(bars, "11:00"), flat_by=time(15, 15), now=NOW)
    assert o.status is Status.UNAVAILABLE and o.note == "gap in intraday bars (missing after 10:45)"


def test_intraday_gap_then_time_exit_unavailable():
    bars = _session({"09:45": (99.5, 100.1, 99, 99.5)})
    o = resolve_intraday(_ic(), _without(bars, "12:00"), flat_by=time(15, 15), now=NOW)
    assert o.status is Status.UNAVAILABLE and o.note.startswith("gap in intraday bars")


def test_intraday_gap_while_unfilled_unavailable():
    o = resolve_intraday(_ic(), _without(_session({}), "12:00"), flat_by=time(15, 15), now=NOW)
    assert o.status is Status.UNAVAILABLE and o.note == "gap in intraday bars (missing after 11:45)"


def test_intraday_first_bar_late_unavailable():
    bars = _session({}, first="10:15")
    o = resolve_intraday(_ic(), bars, flat_by=time(15, 15), now=NOW)
    assert o.status is Status.UNAVAILABLE and o.note.startswith("gap in intraday bars")


def test_intraday_first_bar_one_slot_late_unavailable():
    assert resolve_intraday(_ic(), _session({}, first="10:00"), flat_by=time(15, 15), now=NOW).status \
        is Status.UNAVAILABLE


def test_intraday_complete_session_resolves():
    bars = _session({"09:45": (99.5, 100.1, 99, 99.5)})
    assert resolve_intraday(_ic(), bars, flat_by=time(15, 15), now=NOW).status is Status.TIME


# ---------------------------------------------------------------- review round R1 (scoreboard honesty)

def _unavail(i):
    from nse_backtest.desk.scoreboard import unavailable
    return unavailable(_outs([1.0])[0].candidate.__class__(
        datetime(2026, 6, 1, tzinfo=IST) + pd.Timedelta(days=i).to_pytimedelta(), "NSE", "S", Mode.SWING,
        100, 98, 103, 15, 0, 10, 0.3, None, f"u{i}"), "could not replay (KeyError): 'Low'")


def test_verdict_counts_unavailable_trades():
    # probe_sb: 30 winners and 40 trades that could not be replayed must never read as Matching
    rng = np.random.default_rng(1)
    outs = _outs(list(0.5 + 0.1 * rng.standard_normal(30)), mc=0.3) + [_unavail(i) for i in range(40)]
    s = summarize(outs)[0]
    assert s.n == 30 and s.unavailable == 40
    assert s.verdict == "Inconclusive — 40 trades could not be replayed; fix the data before trusting this"


def test_verdict_unavailable_gate_threshold():
    winners = _outs([0.30] * 36, mc=0.30)
    assert summarize(winners + [_unavail(i) for i in range(4)])[0].verdict.startswith("Matching")  # 4/40 = 10%
    s = summarize(winners + [_unavail(i) for i in range(5)])[0]  # 5/41 > 10%
    assert s.verdict.startswith("Inconclusive — 5 trades could not be replayed")


def test_verdict_losing_beats_unavailable_gate():
    s = summarize(_outs([-1.0, -0.5] * 20) + [_unavail(i) for i in range(40)])[0]
    assert s.verdict == "Losing — do not trade this with real money"


def test_verdict_unavailable_gate_before_coverage_gate_and_small_n_first():
    s = summarize(_outs([0.30] * 40) + [_unavail(i) for i in range(10)])[0]
    assert s.verdict.startswith("Inconclusive — 10 trades could not be replayed")
    s = summarize(_outs([0.30] * 10, mc=0.3) + [_unavail(i) for i in range(10)])[0]
    assert s.verdict.startswith("Not enough trades yet (10/30)")


# ---- swing bar gaps: a missing session is never walked over
GAP_CREATED = datetime(2026, 9, 1, 18, 0, tzinfo=IST)


def _gap_frame():
    days = pd.bdate_range("2026-09-02", periods=8)
    rows = {"Open": [101, 100, 99, 97, 104, 106, 108, 109], "High": [102, 101, 100, 98, 106, 108, 111, 112],
            "Low": [99.5, 99, 98, 93, 103, 105, 107, 108], "Close": [100, 99, 98, 94, 105, 107, 110, 111]}
    return pd.DataFrame(rows, index=days), days


def _gap_cand():
    return Candidate(GAP_CREATED, "NSE", "FAKE", Mode.SWING, 100.0, 95.0, 110.0, 15, 0.1, 10, 0.3, None, "h|x")


def test_swing_missing_stop_bar_is_unavailable():
    full, days = _gap_frame()
    assert resolve_swing(_gap_cand(), full, fill_session=date(2026, 9, 2), now=NOW).status is Status.LOST
    o = resolve_swing(_gap_cand(), full.drop(days[3]), fill_session=date(2026, 9, 2), now=NOW)
    assert o.status is Status.UNAVAILABLE and o.realised_r is None
    assert o.note == "missing daily bar for 2026-09-07"


def test_swing_gap_after_exit_is_harmless_and_holiday_is_not_a_gap():
    full, days = _gap_frame()
    o = resolve_swing(_gap_cand(), full.drop(days[6]), fill_session=date(2026, 9, 2), now=NOW)
    assert o.status is Status.LOST and o.note == ""  # exit on the 7th, the gap is after it
    # 2026-10-20 is an NSE holiday: Mon 19 -> Wed 21 is consecutive
    idx = pd.DatetimeIndex(["2026-10-19", "2026-10-21"])
    df = pd.DataFrame([(101, 101.5, 99.5, 100.5), (100.5, 103.2, 100.1, 103)], index=idx,
                      columns=["Open", "High", "Low", "Close"])
    c = cand(created=datetime(2026, 10, 16, 18, 0, tzinfo=IST))
    assert resolve_swing(c, df, fill_session=date(2026, 10, 19), now=NOW).status is Status.WON


def test_swing_gap_while_open_is_unavailable():
    df = daily([(101, 101.5, 99.5, 100.5), (100.5, 102, 99.5, 101), (100.5, 102, 99.5, 101)])
    df = df.drop(df.index[1])
    o = resolve_swing(cand(hold=5), df, fill_session=FILL, now=NOW)
    assert o.status is Status.UNAVAILABLE and o.note == "missing daily bar for 2026-10-28"


def test_swing_uncovered_year_notes_say_so():
    c = cand(created=datetime(2027, 3, 1, 18, 0, tzinfo=IST))
    df = daily([(101, 101.5, 99.5, 100.5), (100.5, 103.2, 100.1, 103)], start="2027-03-02")
    o = resolve_swing(c, df, fill_session=date(2027, 3, 2), now=datetime(2027, 4, 1, 12, 0, tzinfo=IST))
    assert o.status is Status.WON and o.note == "(holiday calendar not available)"
    gap = df.drop(df.index[1:2])
    gap = pd.concat([gap, daily([(100.5, 103.2, 100.1, 103)], start="2027-03-04")])
    o = resolve_swing(c, gap, fill_session=date(2027, 3, 2), now=datetime(2027, 4, 1, 12, 0, tzinfo=IST))
    assert o.status is Status.UNAVAILABLE
    assert o.note == "missing daily bar for 2027-03-03 (holiday calendar not available)"


def test_scoreboard_version_bumped_for_stop_limit_rule():
    from nse_backtest.desk.scoreboard import SCOREBOARD_VERSION
    assert SCOREBOARD_VERSION == 10  # F1 round 7 (10): plain needs the containing bar; F1 round 6 (9): the fallback is capped; F1 round 5 (8): containing-bar fills count only if they lose; F1 round 4 (7): swing restored + containing bars; round 3 (6); C1 (3): intraday fills are a stop-limit; C2 (4): cache entries carry the record; F1 I2 (5): plain-LIMIT fills


# ---- price scale: a split/bonus after the ticket must never be replayed on re-scaled prices
from nse_backtest.desk.scoreboard import check_price_scale  # noqa: E402


def _adj_cand(last_close=101.0, last_bar="2026-09-01T00:00:00"):
    return Candidate(datetime(2026, 9, 1, 18, 0, tzinfo=IST), "NSE", "FAKE", Mode.SWING, 100.0, 95.0, 110.0, 15,
                     0.1, 10, 0.3, None, "h|x", last_bar, last_close)


def _adj_raw():
    days = pd.bdate_range("2026-09-01", periods=7)
    return pd.DataFrame({"Open": [100, 101, 102, 105, 108, 56, 57], "High": [101.5, 102, 104, 108, 112, 57, 58],
                         "Low": [99.5, 99.5, 101, 104, 107, 55, 56],
                         "Close": [101, 101, 103, 107, 111, 56.5, 57.5]}, index=days)


def test_price_scale_bonus_after_ticket_is_unavailable():
    raw = _adj_raw()
    assert check_price_scale(_adj_cand(), raw) is None
    assert resolve_swing(_adj_cand(), raw, fill_session=date(2026, 9, 2), now=NOW).status is Status.WON
    adj = raw.astype(float)
    adj.iloc[:5] = adj.iloc[:5] / 2  # 1:1 bonus ex on day 6: history halved, as yfinance serves it
    assert resolve_swing(_adj_cand(), adj, fill_session=date(2026, 9, 2), now=NOW).status is Status.LOST  # the lie
    o = check_price_scale(_adj_cand(), adj)
    assert o.status is Status.UNAVAILABLE
    assert o.note == "prices re-scaled since the ticket (split/bonus?): logged close ₹101.00, now ₹50.50"


def test_price_scale_dividend_under_unadjusted_bars_unchanged():
    raw = _adj_raw()
    raw.loc[raw.index[1], "Low"] = 100.4  # would really not fill
    o = resolve_swing(_adj_cand(), raw, fill_session=date(2026, 9, 2), now=NOW)
    assert check_price_scale(_adj_cand(), raw) is None and o.status is Status.NOT_FILLED
    # dividend-adjusted (x0.985) history would fill a trade that never filled: the scale check catches it
    assert check_price_scale(_adj_cand(), raw * 0.985).status is Status.UNAVAILABLE
    assert resolve_swing(_adj_cand(), raw * 0.985, fill_session=date(2026, 9, 2), now=NOW).status \
        is not Status.NOT_FILLED


def test_price_scale_tolerance_half_percent():
    raw = _adj_raw()
    assert check_price_scale(_adj_cand(last_close=101.0 * 1.0049), raw) is None
    assert check_price_scale(_adj_cand(last_close=101.0 * 1.0051), raw).status is Status.UNAVAILABLE


def test_price_scale_old_row_without_last_close_uses_entry_band():
    adj = _adj_raw().astype(float)
    adj.iloc[:5] = adj.iloc[:5] / 2
    assert check_price_scale(_adj_cand(last_close=None), _adj_raw()) is None
    o = check_price_scale(_adj_cand(last_close=None), adj)
    assert o.status is Status.UNAVAILABLE and o.note.startswith("prices re-scaled since the ticket (split/bonus?)")


def test_price_scale_missing_last_bar_is_unavailable():
    raw = _adj_raw()
    o = check_price_scale(_adj_cand(), raw.drop(raw.index[0]))
    assert o.status is Status.UNAVAILABLE and o.note == "no daily bar for the ticket's last bar 2026-09-01"
    o = check_price_scale(_adj_cand(last_bar=None), raw)
    assert o.status is Status.UNAVAILABLE and "last_bar" in o.note
    assert check_price_scale(_adj_cand(last_bar="garbage"), raw).status is Status.UNAVAILABLE


def test_load_candidates_reads_last_bar_and_last_close():
    row = dict(action="TAKE", market="NSE", symbol="ABC", mode="swing", entry=100, stop=98, target_1=103,
               qty=10, input_hash="h", created_at="2026-10-26T18:00:00+05:30", last_bar="2026-10-26T00:00:00",
               last_close=100.5)
    (c,), _ = load_candidates([row])
    assert c.last_bar == "2026-10-26T00:00:00" and c.last_close == 100.5
    (c,), _ = load_candidates([{k: v for k, v in row.items() if k not in ("last_bar", "last_close")}])
    assert c.last_bar is None and c.last_close is None


def test_audit_writes_last_close(tmp_path):
    from dataclasses import replace
    from nse_backtest.desk import audit
    from nse_backtest.desk.markets import NSE
    from nse_backtest.desk.verdict import decide
    v = replace(decide(P, S, None, OK, NSE, 100.0, VNOW), cost_per_share=0.37)
    rec = json.loads(audit.append_verdict(v, tmp_path / "a.jsonl").read_text())
    assert rec["last_close"] == P.last_close


# ---- swing TAKE logged during its fill session: the daily fill bar holds prices from before the order
MID = datetime(2026, 10, 26, 14, 0, tzinfo=IST)
CLOSED = datetime(2026, 10, 29, 20, 0, tzinfo=IST)


def _mid_cand(created=MID):
    return Candidate(created, "NSE", "FAKE", Mode.SWING, 100.0, 97.0, 104.5, 15, 0.0, 10, 0.3, 0.2, "h|x")


def _mid_daily():
    idx = pd.to_datetime(["2026-10-26", "2026-10-27", "2026-10-28"])
    return pd.DataFrame({"Open": [98.6, 101.0, 104.0], "High": [100.9, 103.0, 105.0], "Low": [98.4, 100.6, 103.5],
                         "Close": [100.7, 102.9, 104.8]}, index=idx)


def _fill_day(over=None, first="14:00", last="15:15", day="2026-10-26"):  # F1 r4: incl. the containing bar
    """15-minute bars of the fill day; idle bars stay above the entry."""
    idle = (100.5, 100.8, 100.3, 100.6)
    rng = pd.date_range(f"{day} {first}", f"{day} {last}", freq="15min")
    over = over or {}
    return pd.DataFrame([over.get(f"{t:%H:%M}", idle) for t in rng], index=rng,
                        columns=["Open", "High", "Low", "Close"])


def test_mid_session_swing_needs_intraday_bars():
    o = resolve_swing(_mid_cand(), _mid_daily(), fill_session=date(2026, 10, 26), now=CLOSED,
                      session_closed_today=True)
    assert o.status is Status.UNAVAILABLE
    assert o.note.startswith("TAKE logged mid-session; intraday bars needed for the fill day")


def test_mid_session_swing_probe1_not_filled():
    # probe1: the daily Low 98.4 came before the 14:00 TAKE; afterwards price never came back to 100
    bars = _fill_day()
    o = resolve_swing(_mid_cand(), _mid_daily(), fill_session=date(2026, 10, 26), now=CLOSED,
                      session_closed_today=True, bars15=bars)
    assert o.status is Status.NOT_FILLED and o.realised_r is None


def test_mid_session_swing_fill_then_target():
    bars = _fill_day({"14:30": (99.9, 100.2, 99.7, 100.1), "15:00": (100.5, 106.0, 100.4, 105.0)})
    o = resolve_swing(_mid_cand(), _mid_daily(), fill_session=date(2026, 10, 26), now=CLOSED,
                      session_closed_today=True, bars15=bars)
    assert o.status is Status.WON and o.fill_price == pytest.approx(99.9)  # min(Open, entry) of the 15-min bar
    assert o.fill_at.startswith("2026-10-26 14:30") and o.exit_price == 104.5
    assert o.exit_at.startswith("2026-10-28") and o.bars_held == 2  # the 106 high on the fill day is ignored


def test_mid_session_swing_stop_after_fill_on_fill_day():
    bars = _fill_day({"14:30": (100.2, 100.3, 99.8, 100.0), "15:00": (96.5, 97.0, 96.0, 96.8)})
    o = resolve_swing(_mid_cand(), _mid_daily(), fill_session=date(2026, 10, 26), now=CLOSED,
                      session_closed_today=True, bars15=bars)
    assert o.status is Status.LOST and o.fill_price == 100.0 and o.exit_price == 96.5
    assert o.exit_at.startswith("2026-10-26 15:00")


def test_mid_session_swing_gap_or_short_fill_day_unavailable():
    gap = _fill_day().drop(pd.Timestamp("2026-10-26 14:45"))
    o = resolve_swing(_mid_cand(), _mid_daily(), fill_session=date(2026, 10, 26), now=CLOSED,
                      session_closed_today=True, bars15=gap)
    assert o.status is Status.UNAVAILABLE and "gap in intraday bars" in o.note
    short = _fill_day(last="14:45")
    o = resolve_swing(_mid_cand(), _mid_daily(), fill_session=date(2026, 10, 26), now=CLOSED,
                      session_closed_today=True, bars15=short)
    assert o.status is Status.UNAVAILABLE and "incomplete" in o.note
    o = resolve_swing(_mid_cand(), _mid_daily(), fill_session=date(2026, 10, 26), now=CLOSED,
                      session_closed_today=True, bars15=_fill_day(day="2026-10-23"))
    assert o.status is Status.UNAVAILABLE


def test_swing_before_open_or_after_close_keeps_daily_rule():
    pre = _mid_cand(datetime(2026, 10, 26, 9, 0, tzinfo=IST))
    o = resolve_swing(pre, _mid_daily(), fill_session=date(2026, 10, 26), now=CLOSED, session_closed_today=True)
    assert o.status is Status.WON and o.fill_price == 98.6
    from nse_backtest.desk.scoreboard import logged_mid_session
    assert not logged_mid_session(pre, date(2026, 10, 26))
    assert logged_mid_session(_mid_cand(), date(2026, 10, 26))
    after = _mid_cand(datetime(2026, 10, 26, 16, 0, tzinfo=IST))
    assert not logged_mid_session(after, date(2026, 10, 27))


# ---- intraday fill = the printed ticket: BUY stop-limit, trigger at entry, limit at entry_limit
def test_intraday_limit_fill_probe3_not_filled():
    c = Candidate(datetime(2026, 10, 26, 10, 0, tzinfo=IST), "NSE", "FAKE", Mode.INTRADAY, 100.0, 99.0, 101.5, 20,
                  0.0, 10, 0.2, None, "h|x")
    idx = pd.date_range("2026-10-26 10:00", "2026-10-26 15:00", freq="15min")
    op = [100.8 + 0.05 * i for i in range(len(idx))]
    bars = pd.DataFrame({"Open": op, "High": [x + 0.3 for x in op], "Low": [x - 0.2 for x in op],
                         "Close": [x + 0.05 for x in op]}, index=idx)
    o = resolve_intraday(c, bars, flat_by=time(15, 15), now=datetime(2026, 10, 27, 9, 0, tzinfo=IST))
    assert o.status is Status.NOT_FILLED  # triggered above the 100.25 limit and never back to it: no fill


def test_intraday_gap_above_limit_rests_and_fills_at_the_limit():
    c = _ic()
    bars = _session({"09:30": (99, 99.5, 98.9, 99.2), "10:00": (99.6, 100.0, 99.4, 99.8), "10:15": (99.9, 103.2, 99.8, 103)},
                    idle=(101, 101.5, 100.5, 101))  # F1 r4: the 09:30 containing bar stays below the trigger
    o = resolve_intraday(c, bars, flat_by=time(15, 15), now=NOW)
    # 09:45 opens at 101 above the 100.5 limit: the order rests and fills at the limit (Low 100.5), never better
    assert o.status is Status.WON and o.fill_price == pytest.approx(100.5) and o.fill_at.startswith("2026-10-27 09:45")


def test_intraday_uses_the_logged_entry_limit():
    c = _ic()
    bars = _session({"09:45": (100.3, 100.6, 100.25, 100.4), "10:00": (100.4, 100.5, 100.15, 100.3),
                     "10:15": (100.3, 103.2, 100.25, 103)})
    o = resolve_intraday(c, bars, flat_by=time(15, 15), now=NOW)  # no logged limit: 100.5 computed
    assert o.fill_price == pytest.approx(100.3) and o.fill_at.startswith("2026-10-27 09:45")
    tight = replace(c, entry_limit=100.2)  # 09:45 opens above it: the order rests until 10:00 trades at 100.2
    o = resolve_intraday(tight, bars, flat_by=time(15, 15), now=NOW)
    assert o.fill_price == pytest.approx(100.2) and o.fill_at.startswith("2026-10-27 10:00")


def test_parse_row_uses_early_close_flat_by():
    row = dict(action="TAKE", market="US", symbol="ABC", mode="intraday", entry=100, stop=98, target_1=103,
               qty=10, input_hash="h", created_at="2026-11-27T12:50:00-05:00")  # early close 13:00, flat-by 12:45
    cands, rejects = load_candidates([row])
    assert not cands and rejects[0][1] == "created after flat-by"
    cands, _ = load_candidates([dict(row, created_at="2026-11-27T12:40:00-05:00")])
    assert len(cands) == 1


# ---- review round R1 follow-up: a stop exit is never above the bar's open; LOST never books a gain
def test_intraday_fill_bar_opening_below_stop_exits_at_the_stop():
    # probe_b: the first bar after the TAKE opens at 97 (below the 98 stop) and trades up through the 100 trigger:
    # filled at 100; the stop order exists only after that, so the later stop touch exits at the stop 98
    c = Candidate(datetime(2026, 10, 27, 10, 5, tzinfo=IST), "NSE", "ABC", Mode.INTRADAY, 100.0, 98.0, 103.0, 20,
                  0.0, 10, None, None, "h|x")
    rows = [(101, 101, 100.5, 101)] * 3 + [(99, 99.5, 98.9, 99.2)] + [(97, 100.2, 96.5, 98.5)] + [(98.5, 99, 98, 98.5)] * 20
    # F1 r4: the 10:00 bar contains the 10:05 TAKE and stays below the trigger
    idx = pd.date_range("2026-10-27 09:15", periods=len(rows), freq="15min")
    bars = pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close"])
    o = resolve_intraday(c, bars, flat_by=time(15, 10), now=datetime(2026, 10, 28, 12, 0, tzinfo=IST))
    assert o.status is Status.LOST and o.fill_price == 100 and o.exit_price == 98
    assert o.realised_r == pytest.approx(-1.0) and summarize([o])[0].wins == 0


def _random_bars(rng, n, base=100.0, spread=4.0):
    """n OHLC rows around `base` with wide moves: opens below stops, gaps through targets, both touched."""
    op = base + rng.uniform(-spread, spread, n)
    cl = base + rng.uniform(-spread, spread, n)
    hi = np.maximum(op, cl) + rng.uniform(0, 0.75 * spread, n)
    lo = np.minimum(op, cl) - rng.uniform(0, 0.75 * spread, n)
    return np.column_stack([op, hi, lo, cl])


def _check_invariants(c, o):
    if o.status is Status.LOST:
        assert o.exit_price <= c.stop + 1e-9 and o.exit_price <= o.fill_price + 1e-9
        assert o.realised_r + c.cost_per_share / (c.entry - c.stop) <= 1e-9  # no gain before costs
    if o.status is Status.WON:
        assert o.exit_price >= c.target_1 - 1e-9
    if o.fill_price is not None and c.mode is Mode.INTRADAY:  # a BUY stop-limit fills inside [trigger, limit]
        limit = c.entry_limit or entry_limit(c.entry, c.stop, get_profile(c.market).tick)
        assert c.entry - 1e-9 <= o.fill_price <= limit + 1e-9
    elif o.fill_price is not None:
        assert o.fill_price <= c.entry + 1e-9  # a BUY LIMIT never pays above its limit


def test_property_exits_respect_stop_and_target():
    rng = np.random.default_rng(19)
    days = pd.bdate_range("2026-11-02", periods=20)
    days = days[[cal_ok for cal_ok in [d.date() != date(2026, 11, 10) and d.date() != date(2026, 11, 24)
                                      for d in days]]]
    seen = set()
    for trial in range(400):
        # swing, daily rule (created after the close the day before the first bar)
        c = Candidate(datetime(2026, 10, 30, 18, 0, tzinfo=IST), "NSE", "P", Mode.SWING, 100.0, 98.0, 103.0,
                      int(rng.integers(1, 8)), 0.05, 10, None, None, f"s{trial}")
        df = pd.DataFrame(_random_bars(rng, len(days)), index=days, columns=["Open", "High", "Low", "Close"])
        o = resolve_swing(c, df, fill_session=days[0].date(), now=NOW + pd.Timedelta(days=30).to_pytimedelta())
        _check_invariants(c, o)
        seen.add(("swing", o.status))
        # swing logged mid-session: fill day from 15-minute bars
        cm = Candidate(datetime(2026, 11, 2, 11, 0, tzinfo=IST), "NSE", "P", Mode.SWING, 100.0, 98.0, 103.0,
                       int(rng.integers(1, 8)), 0.05, 10, None, None, f"m{trial}")
        idx = pd.date_range("2026-11-02 11:00", "2026-11-02 15:15", freq="15min")  # F1 r4: + the containing bar
        b15 = pd.DataFrame(_random_bars(rng, len(idx), base=100.5, spread=float(rng.choice([0.6, 3.0]))),
                           index=idx, columns=["Open", "High", "Low", "Close"])
        o = resolve_swing(cm, df, fill_session=days[0].date(), now=NOW + pd.Timedelta(days=30).to_pytimedelta(),
                          bars15=b15)
        _check_invariants(cm, o)
        seen.add(("mid", o.status))
        # intraday
        ci = Candidate(datetime(2026, 11, 2, 9, 31, tzinfo=IST), "NSE", "P", Mode.INTRADAY, 100.0, 98.0, 103.0, 26,
                       0.05, 10, None, None, f"i{trial}")
        idx = pd.date_range("2026-11-02 09:30", "2026-11-02 15:00", freq="15min")  # F1 r4: + the containing bar
        bi = pd.DataFrame(_random_bars(rng, len(idx)), index=idx, columns=["Open", "High", "Low", "Close"])
        o = resolve_intraday(ci, bi, flat_by=time(15, 15), now=NOW + pd.Timedelta(days=30).to_pytimedelta())
        _check_invariants(ci, o)
        seen.add(("intraday", o.status))
    for kind in ("swing", "mid", "intraday"):  # the grid really reaches losses and wins on every path
        assert (kind, Status.LOST) in seen and (kind, Status.WON) in seen


# ---- intraday price scale: yfinance split-adjusts 15-minute bars too
from nse_backtest.desk.scoreboard import check_intraday_price_scale  # noqa: E402


def _ic_scaled(last_close=100.6, last_bar="2026-10-27T10:00:00"):
    return Candidate(datetime(2026, 10, 27, 10, 5, tzinfo=IST), "NSE", "ABC", Mode.INTRADAY, 100.0, 98.0, 103.0,
                     20, 0.0, 10, None, None, "h|x", last_bar, last_close)


def _ic_bars(scale=1.0):
    idx = pd.date_range("2026-10-27 09:15", periods=24, freq="15min")
    return pd.DataFrame([(100.5, 100.8, 100.2, 100.6)] * 24, index=idx, columns=["Open", "High", "Low", "Close"]) * scale


def test_intraday_price_scale_halved_bars_unavailable():
    assert check_intraday_price_scale(_ic_scaled(), _ic_bars()) is None
    o = check_intraday_price_scale(_ic_scaled(), _ic_bars(0.5))  # probe_a P2: 1:1 bonus inside 58 days
    assert o.status is Status.UNAVAILABLE and o.note.startswith("prices re-scaled since the ticket (split/bonus?)")
    o = check_intraday_price_scale(_ic_scaled(last_close=None), _ic_bars(0.5))
    assert o.status is Status.UNAVAILABLE and o.note.startswith("prices re-scaled since the ticket")
    assert check_intraday_price_scale(_ic_scaled(last_close=None), _ic_bars()) is None


def test_intraday_price_scale_partial_bar_close_inside_final_range_is_fine():
    # the plan's last 15-minute bar was still forming: its close then is any price inside the final bar's range
    assert check_intraday_price_scale(_ic_scaled(last_close=100.25), _ic_bars()) is None
    assert check_intraday_price_scale(_ic_scaled(last_close=101.5), _ic_bars()).status is Status.UNAVAILABLE


def test_intraday_price_scale_needs_the_last_bar():
    o = check_intraday_price_scale(_ic_scaled(last_bar="2026-10-27T08:00:00"), _ic_bars())
    assert o.status is Status.UNAVAILABLE and "last bar" in o.note
    assert check_intraday_price_scale(_ic_scaled(last_bar=None), _ic_bars()).status is Status.UNAVAILABLE


# ---- C1: intraday rows carry the stop-limit's limit price
def test_load_candidates_reads_or_computes_entry_limit():
    base = dict(action="TAKE", market="NSE", symbol="ABC", mode="intraday", entry=100, stop=99.6, target_1=103,
                qty=10, input_hash="h", created_at="2026-10-27T10:00:00+05:30")
    (logged,), _ = load_candidates([dict(base, entry_limit=100.15)])
    (old,), _ = load_candidates([base])
    (swing,), _ = load_candidates([dict(base, mode="swing")])
    assert logged.entry_limit == 100.15
    assert old.entry_limit == pytest.approx(100.10)  # entry + 0.25R = 100.1, on the 0.05 tick
    assert swing.entry_limit is None


@pytest.mark.parametrize("bad", [99.9, float("nan"), "x", 100.6])  # 100.6: above the formula's 100.5 + one tick
def test_load_candidates_rejects_bad_entry_limit(bad):
    row = dict(action="TAKE", market="NSE", symbol="ABC", mode="intraday", entry=100, stop=98, target_1=103,
               qty=10, input_hash="h", created_at="2026-10-27T10:00:00+05:30", entry_limit=bad)
    cands, rejects = load_candidates([row])
    assert not cands and "entry_limit" in rejects[0][1]


def test_load_candidates_accepts_entry_limit_up_to_one_tick_over_the_formula():
    row = dict(action="TAKE", market="NSE", symbol="ABC", mode="intraday", entry=100, stop=98, target_1=103,
               qty=10, input_hash="h", created_at="2026-10-27T10:00:00+05:30", entry_limit=100.55)
    (c,), _ = load_candidates([row])
    assert c.entry_limit == 100.55


def test_audit_writes_entry_limit(tmp_path):
    from nse_backtest.desk import audit
    from nse_backtest.desk.markets import NSE
    from nse_backtest.desk.verdict import decide
    PI = replace(P, mode=Mode.INTRADAY, entry_limit=100.5)
    rec = json.loads(audit.append_verdict(decide(PI, S, None, OK, NSE, 100.0, VNOW), tmp_path / "i.jsonl").read_text())
    assert rec["entry_limit"] == 100.5
    rec = json.loads(audit.append_verdict(decide(P, S, None, OK, NSE, 100.0, VNOW), tmp_path / "s.jsonl").read_text())
    assert rec["entry_limit"] is None


# ---- C1 fix round: the scoreboard fills only inside the desk's entry window, like the Time Machine
def test_intraday_trigger_after_the_entry_window_is_not_filled():
    late = _session({"14:45": (100.1, 100.4, 100.0, 100.3), "15:00": (100.3, 103.5, 100.2, 103)})
    o = resolve_intraday(_ic(), late, flat_by=time(15, 15), now=NOW)
    assert o.status is Status.NOT_FILLED and o.note == "not filled inside the entry window (cancel by 14:45)"


def test_intraday_resting_limit_back_after_the_entry_window_is_not_filled():
    # 14:15 triggers above the 100.5 limit; price is back to the limit only at 14:45, after the window
    bars = _session({"14:15": (101, 101.5, 100.8, 101), "14:30": (101, 101.4, 100.7, 101),
                     "14:45": (100.6, 100.7, 100.2, 100.4)}, idle=(99, 99.5, 98.9, 99.2))
    o = resolve_intraday(_ic(), bars, flat_by=time(15, 15), now=NOW)
    assert o.status is Status.NOT_FILLED and o.note == "not filled inside the entry window (cancel by 14:45)"
    inside = _session({"14:15": (101, 101.5, 100.8, 101), "14:30": (100.6, 100.7, 100.2, 100.4)})
    o = resolve_intraday(_ic(), inside, flat_by=time(15, 15), now=NOW)
    assert o.fill_price == pytest.approx(100.5) and o.fill_at.startswith("2026-10-27 14:30")


# ---- C2: owner overrides are their own record, never pooled with the desk's picks
def _row(action, h="h", created="2026-10-27T10:00:00+05:30", **kw):
    return {**dict(action=action, market="NSE", symbol="ABC", mode="swing", entry=100, stop=98, target_1=103,
                   qty=10, input_hash=h, created_at=created), **kw}


def test_load_candidates_take_is_desk_override_is_override_others_ignored():
    rows = [_row("TAKE", "t"), _row("OVERRIDE", "o", symbol="XYZ", desk_action="SKIP", chosen_by="owner"),
            _row("WAIT", "w", symbol="W1"), _row("SKIP", "s", symbol="S1"), _row("take", "x", symbol="LC")]
    cands, rejects = load_candidates(rows)
    assert rejects == [] and [(c.symbol, c.record) for c in cands] == [("ABC", "desk"), ("XYZ", "override")]


def test_load_candidates_dedupe_is_per_record():
    rows = [_row("TAKE", "t"),
            _row("OVERRIDE", "t", desk_action="WAIT"),  # same ticket, same instant: two records, two candidates
            # same session, same record: deduped
            _row("OVERRIDE", "o2", created="2026-10-27T11:00:00+05:30", desk_action="WAIT")]
    cands, _ = load_candidates(rows)
    assert sorted(c.record for c in cands) == ["desk", "override"]
    desk, over = sorted(cands, key=lambda c: c.record)
    assert desk.key != over.key  # one cache slot each
    assert over.created_at.hour == 10  # earliest override kept


def test_candidate_record_defaults_to_desk():
    assert cand().record == "desk"


def test_summarize_never_pools_records():
    desk = _outs([1.0] * 3)
    over = [replace(o, candidate=replace(o.candidate, record="override", key=o.candidate.key + "|o"))
            for o in _outs([-1.0] * 2)]
    stats = summarize(over + desk)
    assert [(s.record, s.market, s.mode, s.n) for s in stats] == [("desk", "NSE", Mode.SWING, 3),
                                                                  ("override", "NSE", Mode.SWING, 2)]
    assert stats[0].mean_r == pytest.approx(1.0) and stats[1].mean_r == pytest.approx(-1.0)
    (only,) = summarize(over)
    assert only.record == "override" and only.n == 2


def test_score_stats_record_is_the_last_field_defaulting_to_desk():
    from dataclasses import fields
    from nse_backtest.desk.scoreboard import ScoreStats
    f = fields(ScoreStats)
    assert f[-1].name == "record" and f[-1].default == "desk"
    assert summarize(_outs([1.0]))[0].record == "desk"


def test_c8_override_of_a_take_verdict_is_rejected():
    base = dict(market="NSE", symbol="ABC", mode="swing", entry=100, stop=98, target_1=103, qty=10,
                input_hash="h", created_at="2026-10-26T10:00:00+05:30", action="OVERRIDE")
    rows = [dict(base, desk_action="TAKE"), dict(base, symbol="W", desk_action="WAIT"),
            dict(base, symbol="S", desk_action="SKIP"), dict(base, symbol="N")]
    cands, rejects = load_candidates(rows)
    assert sorted(c.symbol for c in cands) == ["S", "W"]
    assert len(rejects) == 2 and all("override of a TAKE verdict" in why for _, why in rejects)


def test_c8_two_scans_of_the_same_take_are_one_candidate():
    base = dict(action="TAKE", market="NSE", symbol="ABC", mode="swing", entry=100, stop=98, target_1=103, qty=10,
                input_hash="h")
    cands, rejects = load_candidates([dict(base, created_at="2026-10-26T10:00:00+05:30"),
                                      dict(base, created_at="2026-10-26T10:30:00+05:30", input_hash="h2")])
    assert len(cands) == 1 and rejects == [] and cands[0].record == "desk"


# ---------------------------------------------------------------- F1 round 2, I2: the plain-LIMIT ticket

def _limit_cand(current_price, created=datetime(2026, 10, 27, 10, 7, tzinfo=IST)):
    from dataclasses import replace as _replace
    return _replace(cand(mode=Mode.INTRADAY, created=created), entry_limit=100.5, current_price=current_price)


def test_i2_plain_limit_fills_at_the_logged_price_and_a_failed_breakout_is_lost():
    """p_limit: logged at 10:07 with the price 100.2 (>= trigger 100) -> `BUY MIS LIMIT 100.50` fills at 100.2 at
    once; the next bar breaks the stop -> LOST about -1.1R (it used to be NOT_FILLED)."""
    # F1 r7: the TAKE's bar traded at the logged price (a plain fill needs that)
    bars = _session({"10:00": (100.0, 100.3, 99.9, 100.2), "10:15": (99.6, 99.8, 97.9, 98.2)}, first="09:15",
                    idle=(98.5, 99.0, 98.2, 98.6))
    o = resolve_intraday(_limit_cand(100.2), bars, flat_by=time(15, 15), now=NOW)
    assert o.status is Status.LOST and o.fill_price == pytest.approx(100.2) and o.exit_price == 98.0
    assert o.realised_r == pytest.approx(-1.1)
    assert o.fill_at.startswith("2026-10-27") and "10:07" in o.fill_at


def test_i2_plain_limit_winner_from_the_logged_fill():
    bars = _session({"10:15": (100.3, 103.2, 100.1, 103.0)}, first="09:15", idle=(100.1, 100.4, 99.9, 100.2))
    o = resolve_intraday(_limit_cand(100.2), bars, flat_by=time(15, 15), now=NOW)
    assert o.status is Status.WON and o.fill_price == pytest.approx(100.2) and o.exit_price == 103.0
    assert o.realised_r == pytest.approx(1.4)
    assert 100.0 <= o.fill_price <= 100.5  # the fill invariant holds


def test_i2_stop_gap_on_the_first_bar_exits_at_its_open():
    bars = _session({"10:15": (97.5, 97.8, 97.0, 97.2)}, first="09:15", idle=(100.1, 100.4, 99.9, 100.2))
    o = resolve_intraday(_limit_cand(100.2), bars, flat_by=time(15, 15), now=NOW)
    assert o.status is Status.LOST and o.exit_price == 97.5  # min(Open, stop): the fill came before this bar


def test_i2_rows_without_a_price_or_above_the_limit_keep_the_stop_limit_model():
    bars = _session({"10:15": (99.6, 99.8, 97.9, 98.2)}, first="09:15", idle=(98.5, 99.0, 98.2, 98.6))
    for cp in (None, 99.9):  # old row / below the trigger (above the limit: a resting LIMIT since round 3)
        o = resolve_intraday(_limit_cand(cp), bars, flat_by=time(15, 15), now=NOW)
        assert o.status is Status.NOT_FILLED, cp


def test_i2_audit_rows_carry_the_current_price_into_the_candidate():
    from nse_backtest.desk.scoreboard import load_candidates
    row = {"created_at": "2026-10-27T10:07:00+05:30", "action": "TAKE", "symbol": "ABC", "market": "NSE",
           "mode": "intraday", "entry": 100.0, "stop": 98.0, "target_1": 103.0, "qty": 10, "input_hash": "h",
           "entry_limit": 100.5, "current_price": 100.2}
    (c,), _ = load_candidates([row])
    assert c.current_price == 100.2
    (old,), _ = load_candidates([{k: v for k, v in row.items() if k != "current_price"}])
    assert old.current_price is None


# ---------------------------------------------------------------- F1 round 3: containing bar, resting LIMIT, window

IDLE = (99.0, 99.5, 98.9, 99.2)


def _r3(cp, over, created=(10, 7), record="desk", first="09:15", **kw):
    from dataclasses import replace as _replace
    c = _replace(cand(mode=Mode.INTRADAY, created=datetime(2026, 10, 27, *created, tzinfo=IST)), entry_limit=100.5,
                 current_price=cp, record=record, **kw)
    return resolve_intraday(c, _session(over, first=first, idle=IDLE), flat_by=time(15, 15), now=NOW)


def test_r3_a_plain_limit_containing_bar_stop_is_lost_and_its_target_never_credited():
    o = _r3(100.2, {"10:00": (100, 100.4, 97.5, 100.3), "10:15": (100.3, 103.2, 100.1, 103)})  # probe A
    assert o.status is Status.LOST and o.fill_price == pytest.approx(100.2) and o.exit_price == 98.0
    o = _r3(100.2, {"10:00": (100, 103.4, 99.9, 100.3)})  # a target inside the containing bar is not credited
    assert o.status is Status.TIME and o.note == "time exit at flat-by"


def test_r3_missing_containing_bar_is_unavailable():
    o = _r3(100.2, {}, first="10:15")  # bars start after created_at's bar
    assert o.status is Status.UNAVAILABLE and "gap" in o.note


def test_r3_b_plain_limit_later_gap_through_the_stop_exits_at_the_open():
    o = _r3(100.2, {"10:15": (97.0, 97.5, 96.5, 97.2)})  # probe B
    assert o.status is Status.LOST and o.exit_price == 97.0


def test_r3_resting_limit_for_a_desk_row_priced_above_the_limit():
    o = _r3(100.6, {"10:15": (99.5, 99.8, 98.5, 98.6), "10:30": (98.6, 98.7, 97.5, 97.6)})  # probe K
    assert o.status is Status.LOST and o.fill_price == 100.5 and o.realised_r == pytest.approx(-1.25)


def test_r3_resting_limit_containing_bar_fill_and_stop():
    o = _r3(100.6, {"10:00": (100.8, 100.9, 97.8, 98.2)})
    assert o.status is Status.LOST and o.fill_price == 100.5 and o.exit_price == 98.0


def test_r3_resting_limit_fills_on_a_later_bar_and_never_better_than_the_limit():
    hi = (100.9, 101.2, 100.7, 101.0)  # above the limit: no fill
    o = _r3(100.6, {"10:00": hi, "10:15": hi, "10:30": (99.8, 100.2, 99.5, 100.0), "10:45": (100.0, 103.2, 99.9, 103)})
    assert o.status is Status.WON and o.fill_price == 100.5  # Open 99.8 < limit: still the limit (worst case)
    assert o.fill_at == "2026-10-27 10:30:00"


def test_r3_resting_limit_never_back_is_not_filled():
    hi = (100.9, 101.2, 100.7, 101.0)
    c_over = {f"{h:02d}:{m:02d}": hi for h in range(9, 16) for m in (0, 15, 30, 45)}
    o = _r3(100.6, c_over)
    assert o.status is Status.NOT_FILLED


def test_r3_every_override_priced_at_or_above_entry_is_a_resting_limit():
    # F1 r6: an override priced inside [entry, limit] is marketable, like a desk row (fill at the logged price);
    # one priced above the limit rests at the limit. The containing bar stays above the limit, so the resting
    # override's fill is confirmed by the 10:15 bar.
    traded = {"10:00": (100.1, 100.3, 100.0, 100.2), "10:15": (100.3, 103.2, 100.1, 103)}  # F1 r7: traded at 100.2
    desk = _r3(100.2, traded)
    mine = _r3(100.2, traded, record="override")
    assert desk.fill_price == pytest.approx(100.2) and mine.fill_price == pytest.approx(100.2)
    over = {"10:00": (100.6, 100.8, 100.6, 100.7), "10:15": (100.3, 103.2, 100.1, 103)}
    rest = _r3(100.6, over, record="override")
    assert rest.fill_price == 100.5 and rest.fill_at == "2026-10-27 10:15:00"
    below = _r3(99.9, {"10:15": (99.9, 100.4, 99.8, 100.2), "10:30": (100.2, 103.2, 100.1, 103)},
                record="override")  # below the trigger: the stop-limit
    assert below.status is Status.WON and below.fill_price == pytest.approx(100.0)


@pytest.mark.parametrize("created", [(14, 55), (9, 20)])  # probes G and H
@pytest.mark.parametrize("cp,record", [(100.2, "desk"), (100.6, "desk"), (100.2, "override")])
def test_r3_limit_rows_logged_outside_the_entry_window_are_not_filled(created, cp, record):
    o = _r3(cp, {"15:00": (100.3, 103.2, 100.1, 103), "09:30": (100.3, 103.2, 100.1, 103)}, created=created,
            record=record)
    assert o.status is Status.NOT_FILLED and o.note == "logged outside the entry window"


def test_r3_property_no_fill_better_than_the_logged_price_or_limit_and_no_skipped_containing_stop():
    rng = np.random.default_rng(19)
    for _ in range(400):
        cp = float(rng.choice([99.9, 100.0, 100.2, 100.5, 100.6, 101.0]))
        record = str(rng.choice(["desk", "override"]))
        over = {}
        for t in pd.date_range("2026-10-27 09:15", "2026-10-27 15:00", freq="15min"):
            o_ = float(rng.uniform(97, 103))
            h, l_ = o_ + float(rng.uniform(0, 2)), o_ - float(rng.uniform(0, 2))
            over[f"{t:%H:%M}"] = (o_, h, l_, float(rng.uniform(l_, h)))
        out = _r3(cp, over, record=record)
        if out.fill_price is not None and cp >= 100.0:  # a buy: "better" is lower
            assert out.fill_price >= min(cp, 100.5) - 1e-9, (cp, record, out)
            assert out.fill_price <= 100.5 + 1e-9
        if out.fill_price is not None and cp >= 100.0:
            cont = over["10:00"]
            filled_in_containing = record == "desk" and cp <= 100.5 or cont[2] <= 100.5
            if filled_in_containing and cont[2] <= 98.0:
                assert out.status is Status.LOST and out.exit_price == 98.0, (cp, record, out)


# ---------------------------------------------------------------- F1 round 4

@pytest.mark.parametrize("hm", [(14, 20), (14, 50), (15, 5)])
@pytest.mark.parametrize("cp", [None, 100.6, 101.5])
def test_r4_swing_fill_day_ignores_the_intraday_limit_models_and_window(hm, cp):
    """p_swing3: a swing CNC LIMIT logged mid-session is never scored by the intraday entry window; the bar that
    contains created_at counts (its Low may come after the TAKE), so the fill-day stop is never lost."""
    from dataclasses import replace as _replace
    bars = _fill_day({"15:00": (100.2, 100.3, 96.5, 96.8)})  # fill at entry 100 then the stop 97
    c = _replace(_mid_cand(datetime(2026, 10, 26, *hm, tzinfo=IST)), current_price=cp)
    o = resolve_swing(c, _mid_daily(), fill_session=date(2026, 10, 26), now=CLOSED, session_closed_today=True,
                      bars15=bars)
    assert o.status is Status.LOST and o.realised_r == pytest.approx(-1.0) and o.fill_price == 100.0, o


def test_r4_swing_containing_bar_missing_is_unavailable():
    bars = _fill_day({"15:00": (100.2, 100.3, 96.5, 96.8)}).drop(pd.Timestamp("2026-10-26 14:45"))
    o = resolve_swing(_mid_cand(datetime(2026, 10, 26, 14, 50, tzinfo=IST)), _mid_daily(),
                      fill_session=date(2026, 10, 26), now=CLOSED, session_closed_today=True, bars15=bars)
    assert o.status is Status.UNAVAILABLE and "no bar containing 14:50" in o.note


S_BARS = {"10:00": (99.7, 100.3, 97.8, 98.2), "10:15": (98.2, 100.4, 98.1, 100.3), "10:30": (100.3, 103.2, 100.2, 103)}


@pytest.mark.parametrize("cp,record", [(99.8, "desk"), (99.8, "override"), (None, "desk")])  # p_r3 S1-S3
def test_r4_stop_limit_containing_bar_trigger_and_stop_is_lost(cp, record):
    o = _r3(cp, S_BARS, record=record)
    # F1 r7: without a logged price the fill is the worst consistent price, min(bar High 100.3, limit)
    fill = 100.0 if cp is not None else 100.3
    assert o.status is Status.LOST and o.fill_price == pytest.approx(fill) and o.exit_price == 98.0
    assert o.realised_r == pytest.approx((98.0 - fill) / 2)


def test_r4_stop_limit_containing_bar_trigger_without_stop_fills_there_and_walks_on():
    o = _r3(99.8, {"10:00": (99.7, 100.3, 99.6, 100.2), "10:15": (100.2, 103.2, 100.1, 103)})
    # F1 r5: a containing-bar trigger counts only if it loses; this one would win, so the 10:15 bar's own trigger
    # (open 100.2) is the real fill, scored normally (its target is never credited on the fill bar)
    assert o.fill_price == pytest.approx(100.2) and o.fill_at == "2026-10-27 10:15:00"
    lost = _r3(99.8, {"10:00": (99.7, 100.3, 99.6, 100.2), "10:15": (100.2, 100.3, 97.5, 97.8)})
    assert lost.status is Status.LOST and lost.fill_price == 100.0 and lost.fill_at == "2026-10-27T10:07:00+05:30"


def test_r4_stop_limit_missing_containing_bar_is_unavailable():
    from dataclasses import replace as _replace
    c = _replace(cand(mode=Mode.INTRADAY, created=datetime(2026, 10, 27, 10, 7, tzinfo=IST)), current_price=99.8)
    bars = _session({"10:15": (100.3, 103.2, 100.1, 103)}, first="09:15", idle=IDLE).drop(
        pd.Timestamp("2026-10-27 10:00"))
    o = resolve_intraday(c, bars, flat_by=time(15, 15), now=NOW)  # p_r3 M2
    assert o.status is Status.UNAVAILABLE and "no bar containing 10:07" in o.note


def test_r4_stop_limit_containing_bar_below_entry_keeps_the_existing_rule():
    o = _r3(99.8, {"10:15": (99.9, 100.4, 99.8, 100.3), "10:30": (100.3, 103.2, 100.2, 103)})
    assert o.status is Status.WON and o.fill_at == "2026-10-27 10:15:00"


def test_r4_early_close_last_bar_reaches_the_containing_bar_path():
    """p_r3 E1: a LIMIT row logged in the last bar before an early flat-by is scored from that bar."""
    from dataclasses import replace as _replace
    c = _replace(cand(mode=Mode.INTRADAY, created=datetime(2026, 10, 27, 12, 50, tzinfo=IST)), entry_limit=100.5,
                 current_price=100.8)
    bars = _session({"12:45": (100.8, 100.9, 100.4, 100.45), "13:00": (100.6, 103.2, 100.4, 103)}, first="09:15",
                    last="13:00", idle=IDLE)
    o = resolve_intraday(c, bars, flat_by=time(13, 0), now=NOW)
    assert "no intraday bars" not in o.note and o.fill_price == 100.5 and o.status is Status.TIME


# ---------------------------------------------------------------- F1 round 5: containing-bar fills count only if they lose

AMBIG = "fill only possible on the bar the TAKE was logged in — counted only if it loses"


def _sw(hm, cp, over):
    from dataclasses import replace as _replace
    c = _replace(_mid_cand(datetime(2026, 10, 26, *hm, tzinfo=IST)), current_price=cp)
    return resolve_swing(c, _mid_daily(), fill_session=date(2026, 10, 26), now=CLOSED, session_closed_today=True,
                         bars15=_fill_day(over))


@pytest.mark.parametrize("hm,cp,over", [((14, 20), 101.5, {"14:15": (99.9, 101.6, 99.9, 101.5)}),   # SW1
                                        ((14, 20), None, {"14:15": (99.9, 101.6, 99.9, 101.5)}),    # SW2
                                        ((15, 25), 101.0, {"15:15": (99.95, 101.2, 99.95, 101.0)})])  # SW3
def test_r5_swing_containing_bar_winner_is_not_filled(hm, cp, over):
    o = _sw(hm, cp, over)
    assert o.status is Status.NOT_FILLED and o.note == AMBIG and o.realised_r is None


def test_r5_swing_marketable_price_is_a_real_fill():
    o = _sw((14, 20), 99.8, {"14:15": (99.7, 100.2, 99.7, 99.9)})  # SW4
    assert o.status is Status.WON and o.fill_price == 100.0


def test_r5_swing_containing_bar_loser_is_kept():
    o = _sw((14, 20), 101.5, {"14:15": (96.0, 101.6, 96.0, 101.5)})  # SW6
    assert o.status is Status.LOST and o.realised_r == pytest.approx(-1.0)


def test_r5_swing_fill_confirmed_by_a_later_bar_is_scored_normally():
    o = _sw((14, 20), 101.5, {"14:15": (101.5, 101.6, 101.4, 101.5), "14:45": (99.2, 99.5, 99.0, 99.4)})
    assert o.status is Status.WON and o.fill_price == pytest.approx(99.2)


I1_BARS = {"10:00": (100.2, 100.3, 99.5, 99.8), "14:45": (99.5, 103.5, 99.4, 103.2)}


@pytest.mark.parametrize("cp", [99.8, None])  # I1, I2
def test_r5_stop_limit_containing_bar_winner_is_not_filled(cp):
    o = _r3(cp, I1_BARS)
    assert o.status is Status.NOT_FILLED and o.note == AMBIG


def test_r5_stop_limit_containing_bar_loser_kept_with_fill_at_created_at():
    o = _r3(99.8, {"10:00": (100.2, 100.3, 97.5, 99.8)})
    assert o.status is Status.LOST and o.fill_at == "2026-10-27T10:07:00+05:30"  # I3: created_at, not 10:00


def test_r5_resting_containing_bar_winner_is_not_filled():
    above = {f"{t:%H:%M}": (100.6, 103.2, 100.6, 103) for t in pd.date_range("2026-10-27 10:15", "2026-10-27 15:00",
                                                                              freq="15min")}
    o = _r3(100.6, {"10:00": (100.4, 100.6, 100.4, 100.5), **above}, record="override")  # never back down
    assert o.status is Status.NOT_FILLED and o.note == AMBIG


def test_r5_stop_limit_logged_after_flat_by_is_outside_the_window():
    o = _r3(99.8, {}, created=(15, 20))  # I7 (the session fixture ends at 15:00)
    assert o.status is Status.NOT_FILLED and o.note == "logged outside the entry window"


def test_r5_property_containing_bar_only_fills_never_win():
    rng = np.random.default_rng(19)
    for _ in range(600):
        cp = rng.choice([None, 99.5, 99.9, 100.0, 100.2, 100.5, 100.6, 101.0])
        record = str(rng.choice(["desk", "override"]))
        over = {}
        for t in pd.date_range("2026-10-27 09:15", "2026-10-27 15:00", freq="15min"):
            o_ = float(rng.uniform(97, 103))
            h, l_ = o_ + float(rng.uniform(0, 2)), o_ - float(rng.uniform(0, 2))
            over[f"{t:%H:%M}"] = (o_, h, l_, float(rng.uniform(l_, h)))
        cpf = None if cp is None else float(cp)
        out = _r3(cpf, over, record=record)
        plain = cpf is not None and 100.0 <= cpf <= 100.5  # F1 r6: overrides priced inside are plain too
        b0 = over["10:00"]
        certain = cpf is None and b0[2] >= 100.0 and b0[1] <= 100.5  # F1 r6: a certain containing-bar fill
        plain = plain or certain
        if out.fill_at == "2026-10-27T10:07:00+05:30" and not plain and out.realised_r is not None:
            assert out.realised_r <= 1e-9, (cpf, record, out)


def test_r5_swing_later_bar_confirms_after_an_ambiguous_containing_bar():
    """SW5: the containing-bar fill would win (+1.5R); the 14:45 bar's real fill would win more (+1.77R). F1 r6: a
    counted result is never better than either history, so it is NOT_FILLED."""
    o = _sw((14, 20), 101.5, {"14:15": (99.9, 101.6, 99.9, 101.5), "14:45": (99.2, 99.5, 99.0, 99.4)})
    assert o.status is Status.NOT_FILLED and o.note == AMBIG


def test_r5_stop_limit_later_bar_confirms_after_an_ambiguous_containing_bar():
    """I3: the 10:15 bar opens at 100.45 above the trigger: a real fill there, scored normally."""
    o = _r3(99.8, {"10:00": (100.2, 100.3, 99.5, 99.8), "10:15": (100.45, 103.5, 100.4, 103.2),
                   "10:30": (103.2, 103.5, 103.0, 103.3)})
    assert o.fill_price == pytest.approx(100.45) and o.fill_at == "2026-10-27 10:15:00"
    assert o.status is Status.WON


def test_r5_resting_later_bar_confirms_after_an_ambiguous_containing_bar():
    o = _r3(100.6, {"10:00": (100.4, 100.6, 100.4, 100.5), "10:15": (100.6, 100.7, 100.3, 100.6),
                    "10:30": (100.6, 103.2, 100.6, 103)})
    assert o.status is Status.WON and o.fill_at == "2026-10-27 10:15:00" and o.fill_price == 100.5


# ---------------------------------------------------------------- F1 round 6: the later-bar fallback is capped

GAP_PATTERN = {"10:15": (102.0, 103.0, 101.9, 102.5), "10:30": (101.0, 101.2, 100.4, 100.9),
               "10:45": (106.0, 106.5, 105.8, 106.2)}


def test_r6_a_swing_later_fill_better_than_the_containing_one_is_not_filled():
    from dataclasses import replace as _replace
    c = _replace(_mid_cand(datetime(2026, 10, 26, 14, 20, tzinfo=IST)), current_price=101.5)
    o = resolve_swing(c, _mid_daily(), fill_session=date(2026, 10, 26), now=CLOSED, session_closed_today=True,
                      bars15=_fill_day({"14:15": (99.9, 101.6, 99.9, 101.5), "14:45": (97.2, 97.5, 97.1, 97.4)}))
    assert o.status is Status.NOT_FILLED and o.note == AMBIG


def test_r6_b_stop_limit_later_fill_better_than_the_containing_one_is_not_filled():
    o = _r3(99.8, {"10:00": (100.2, 100.3, 99.5, 99.8), **GAP_PATTERN})
    assert o.status is Status.NOT_FILLED and o.note == AMBIG


def test_r6_c_resting_later_fill_better_than_the_containing_one_is_not_filled():
    o = _r3(100.6, {"10:00": (100.6, 100.7, 100.4, 100.6), "10:15": (100.7, 103.0, 100.6, 102.8),
                    "10:30": (101.0, 101.2, 100.4, 100.9), "10:45": (106.0, 106.5, 105.8, 106.2)}, record="override")
    assert o.status is Status.NOT_FILLED and o.note == AMBIG


def test_r6_g_a_worse_later_fill_is_counted():
    o = _r3(100.6, {"10:00": (100.6, 100.7, 100.4, 100.6), "10:15": (100.7, 103.0, 100.6, 102.8),
                    "10:30": (101.0, 101.2, 100.4, 100.9)}, record="override")
    assert o.status is Status.TIME and o.fill_at == "2026-10-27 10:30:00" and o.realised_r < 0


def test_r6_d_stop_limit_without_price_containing_bar_inside_trigger_and_limit_is_a_certain_fill():
    o = _r3(None, {"10:00": (100.3, 100.45, 100.2, 100.4), "10:15": (99.6, 100.1, 99.5, 100.0),
                   "10:30": (100.1, 103.2, 100.0, 103.0)})
    assert o.status is Status.WON and o.fill_price == 100.5 and o.realised_r == pytest.approx(1.25)
    assert o.fill_at == "2026-10-27T10:07:00+05:30"


def test_r6_f_override_priced_inside_the_limit_is_marketable_at_the_logged_price():
    o = _r3(100.2, {"10:00": (100.2, 100.3, 100.1, 100.2), "10:15": (100.7, 103.0, 100.6, 102.8),
                    "10:30": (101.0, 101.2, 100.4, 100.9), "10:45": (106.0, 106.5, 105.8, 106.2)}, record="override")
    assert o.status is Status.WON and o.fill_price == pytest.approx(100.2) and o.realised_r == pytest.approx(1.4)
    stop = _r3(100.2, {"10:00": (100.2, 100.3, 97.5, 100.2)}, record="override")  # containing-bar stop rule
    assert stop.status is Status.LOST and stop.exit_price == 98.0


def _containing_path_r(cp, record, over):
    """R of the containing-bar fill path (the order filled on the bar the TAKE was logged in), or None."""
    from nse_backtest.desk import scoreboard as sb
    real = sb._only_if_it_loses
    try:
        sb._only_if_it_loses = lambda c, o: o  # keep the containing-bar outcome as computed: no fallback
        return _r3(cp, over, record=record)
    finally:
        sb._only_if_it_loses = real


def test_r6_property_scored_r_never_beats_the_containing_bar_path():
    rng = np.random.default_rng(23)
    for _ in range(600):
        cp = rng.choice([None, 99.5, 99.9, 100.0, 100.2, 100.5, 100.6, 101.0])
        record = str(rng.choice(["desk", "override"]))
        over = {}
        for t in pd.date_range("2026-10-27 09:15", "2026-10-27 15:00", freq="15min"):
            o_ = float(rng.uniform(97, 104))
            h, l_ = o_ + float(rng.uniform(0, 2)), o_ - float(rng.uniform(0, 2))
            over[f"{t:%H:%M}"] = (o_, h, l_, float(rng.uniform(l_, h)))
        cpf = None if cp is None else float(cp)
        cont = _containing_path_r(cpf, record, over)
        out = _r3(cpf, over, record=record)
        if cont.fill_at == "2026-10-27T10:07:00+05:30" and cont.realised_r is not None and out.realised_r is not None:
            assert out.realised_r <= cont.realised_r + 1e-9, (cpf, record, cont, out)


def test_r6_certain_containing_fill_with_a_positive_time_exit_is_kept():
    """A certain fill (no logged price, containing bar wholly inside [trigger, limit]) is never dropped, even when
    it ends in a positive time exit."""
    up = {f"{t:%H:%M}": (100.8, 101.2, 100.7, 101.0) for t in pd.date_range("2026-10-27 10:15", "2026-10-27 15:00",
                                                                             freq="15min")}
    o = _r3(None, {"10:00": (100.3, 100.45, 100.2, 100.4), **up})
    assert o.status is Status.TIME and o.fill_price == 100.5 and o.realised_r > 0


# ---------------------------------------------------------------- F1 round 7

ALL_ABOVE = {f"{t:%H:%M}": (101.0, 103.2, 100.9, 103.0) for t in pd.date_range("2026-10-27 10:15", "2026-10-27 15:00",
                                                                                freq="15min")}


@pytest.mark.parametrize("record", ["desk", "override"])
def test_r7_a_logged_price_the_containing_bar_never_traded_is_not_a_fill(record):
    """P1'/P2': logged 100.2 (a stale quote), but from the TAKE's bar on every bar traded above the 100.5 limit."""
    o = _r3(100.2, {"10:00": (100.7, 101.0, 100.6, 100.8), **ALL_ABOVE}, record=record)
    assert o.status is Status.NOT_FILLED and o.fill_price is None


@pytest.mark.parametrize("record", ["desk", "override"])
def test_r7_logged_price_far_outside_the_bar_rests_at_the_limit(record):
    """A logged price above everything the TAKE's bar traded (stale; more than 0.5% over its High): the resting
    model, with its ambiguous-fill rule (this one would win and nothing later fills: NOT_FILLED)."""
    o = _r3(100.2, {"10:00": (99.0, 99.6, 98.9, 99.2), **ALL_ABOVE}, record=record)
    assert o.status is Status.NOT_FILLED and o.note == AMBIG


def test_r7_logged_price_inside_the_bar_is_still_plain():
    o = _r3(100.2, {"10:00": (100.1, 100.4, 100.0, 100.2), "10:15": (100.3, 103.2, 100.1, 103)})
    assert o.status is Status.WON and o.fill_price == pytest.approx(100.2)
    near = _r3(100.2, {"10:00": (100.6, 100.9, 100.45, 100.8), "10:15": (100.8, 103.2, 100.7, 103)})  # within 0.5%
    assert near.fill_price == pytest.approx(100.2)


def test_r7_b_stop_limit_without_price_straddling_the_trigger_fills_at_the_worst_consistent_price():
    o = _r3(None, {"10:00": (100.2, 100.3, 97.5, 99.8)})  # P3
    assert o.status is Status.LOST and o.fill_price == pytest.approx(100.3)
    assert o.realised_r == pytest.approx(-1.15)
    low = _r3(99.8, {"10:00": (100.2, 100.3, 97.5, 99.8)})  # a logged price below the trigger: fills at the trigger
    assert low.fill_price == 100.0


def test_r7_cap_fallback_passes_open_and_unavailable_through():
    from nse_backtest.desk.scoreboard import _cap_fallback, _outcome
    c = cand(mode=Mode.INTRADAY)
    cont = Outcome(c, Status.WON, 100.0, "x", 103.0, "y", 1.5, None, 1, "")
    for st in (Status.OPEN, Status.UNAVAILABLE):
        later = _outcome(c, st, "later")
        assert _cap_fallback(c, cont, later) is later
