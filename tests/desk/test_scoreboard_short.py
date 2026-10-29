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


def _canon(v):
    """Round floats so the pin survives last-bit differences between platforms (numpy SIMD / summation order)."""
    if isinstance(v, float):
        return round(v, 9)
    if isinstance(v, (tuple, list)):
        return tuple(_canon(x) for x in v)
    return v


def _digest(outs) -> str:
    h = hashlib.sha256()
    for o in outs:
        h.update(repr(_canon(tuple(getattr(o, k) for k in _OUTCOME_FIELDS))).encode())
    for s in summarize(outs):
        h.update(repr(_canon(tuple(getattr(s, k) for k in _STATS_FIELDS))).encode())
    return h.hexdigest()


def test_h5c_long_scoring_is_pinned():
    outs = _long_fixture_outcomes()
    seen = {o.status for o in outs}
    assert {Status.WON, Status.LOST, Status.TIME, Status.NOT_FILLED, Status.OPEN, Status.UNAVAILABLE} <= seen
    assert _digest(outs) == "c89d1f0febbfad9dec51f8a5bbcebedf3449a926b09cafeb110a920daa9c1951"  # same digest on the pre-short base code (10eeb9f)


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
        h.update(repr(_canon(tuple(getattr(c, k) for k in _CAND_FIELDS))).encode())
    for _, why in bad:
        h.update(why.encode())
    assert (len(good), len(bad)) == (4, 4) and h.hexdigest() == "2493bcab7fe6d0ff564eb0dc19c7d6bc2f9b53bc1ea7596f6fe0cdad4e546a33"


# ---------------------------------------------------------------- short fixtures (a mirror of test_scoreboard's)
from nse_backtest.desk import scoreboard as sb  # noqa: E402
from nse_backtest.desk.fills import entry_limit_short, stop_limit_fill_short  # noqa: E402
from nse_backtest.desk.markets import get_profile  # noqa: E402
from nse_backtest.desk.models import Side  # noqa: E402

NSE = get_profile("NSE")
E, SL, T1, LIM = 100.0, 102.0, 97.0, 99.5   # sell trigger, stop above, target below, sell limit below the trigger
SIDLE = (101.0, 101.1, 100.5, 100.8)        # the mirror (200 - p) of test_scoreboard.IDLE
AMBIG = sb.AMBIGUOUS_FILL_NOTE


def _bars(spec):
    idx = pd.to_datetime([f"2026-10-27 {t}" for t, *_ in spec])
    return pd.DataFrame([r for _, *r in spec], index=idx, columns=["Open", "High", "Low", "Close"])


def _session(over, first="09:15", last="15:00", idle=SIDLE):
    rng = pd.date_range(f"2026-10-27 {first}", f"2026-10-27 {last}", freq="15min")
    return _bars([(f"{t:%H:%M}", *over.get(f"{t:%H:%M}", idle)) for t in rng])


def scand(created=(10, 7), cps=0.0, cp=None, record="desk", quote_live=None, quote_as_of=None, **kw):
    at = datetime(2026, 10, 27, *created, tzinfo=IST)
    if quote_live is True and quote_as_of is None:
        quote_as_of = at  # a live quote taken at the TAKE unless a test says otherwise
    return Candidate(at, "NSE", "ABC", Mode.INTRADAY, E, SL, T1, 26, cps, 10, None, None, f"hs|{at.isoformat()}",
                     **{"entry_limit": LIM, "record": record, "current_price": cp, "quote_live": quote_live,
                        "quote_as_of": quote_as_of, "side": Side.SHORT, **kw})


def _s3(cp, over=None, created=(10, 7), record="desk", first="09:15", quote_live=None, quote_as_of=None,
        now=NOW, **kw):
    c = scand(created, cp=cp, record=record, quote_live=quote_live, quote_as_of=quote_as_of, **kw)
    return sb.resolve_intraday(c, _session(over or {}, first=first), flat_by=time(15, 15), now=now)


# ---------------------------------------------------------------- candidates

def _srow(**kw):
    r = {"created_at": "2026-10-27T10:07:00+05:30", "action": "TAKE", "symbol": "ABC", "market": "NSE",
         "mode": "intraday", "entry": 100.0, "stop": 102.0, "target_1": 97.0, "qty": 10, "input_hash": "hs",
         "entry_limit": 99.5, "side": "short", "cost_per_share": 0.1}
    r.update(kw)
    return r


def test_h5c_short_rows_parse_with_their_side():
    (c,), bad = load_candidates([_srow()])
    assert bad == [] and c.side is Side.SHORT and (c.entry, c.stop, c.target_1, c.entry_limit) == (100, 102, 97, 99.5)
    (lg,), _ = load_candidates([_srow(side="long", stop=98.0, target_1=103.0, entry_limit=100.5)])
    assert lg.side is Side.LONG
    (old,), _ = load_candidates([{k: v for k, v in _srow(stop=98.0, target_1=103.0, entry_limit=100.5).items()
                                  if k != "side"}])
    assert old.side is Side.LONG  # rows without a side are longs (every row before H5a)


def test_h5c_short_limit_defaults_to_the_sell_formula_and_is_bounded():
    (c,), _ = load_candidates([_srow(entry_limit=None)])
    assert c.entry_limit == entry_limit_short(100.0, 102.0, NSE.tick) == 99.5
    (c,), _ = load_candidates([_srow(entry_limit=99.45)])  # one tick under the formula: accepted
    assert c.entry_limit == 99.45
    for bad in (100.1, 99.4, float("nan"), "x", 100.5):  # above the trigger, too deep, junk, a long's limit
        good, why = load_candidates([_srow(entry_limit=bad)])
        assert good == [] and "invalid entry_limit" in why[0][1], bad


@pytest.mark.parametrize("kw,reason", [
    ({"stop": 98.0, "target_1": 103.0}, "invalid short geometry"),       # a long's geometry on a short row
    ({"target_1": 100.5}, "invalid short geometry"),
    ({"stop": 100.0}, "invalid short geometry"),
    ({"target_1": -1.0}, "invalid short geometry"),
    ({"mode": "swing"}, "shorts are intraday only"),
    ({"market": "US"}, "shorts are NSE only"),
    ({"side": "sideways"}, "unknown side"),
    ({"created_at": "2026-10-27T15:10:00+05:30"}, "created after the short square-off"),
])
def test_h5c_malformed_short_rows_are_rejected(kw, reason):
    good, bad = load_candidates([_srow(**kw)])
    assert good == [] and reason in bad[0][1]


@pytest.mark.parametrize("name", ["short_fno", "ban_list"])
@pytest.mark.parametrize("action", [{"action": "OVERRIDE", "desk_action": "SKIP"}, {"action": "TAKE"}])
def test_h5c_safety_blocked_shorts_are_never_replayed(name, action):
    good, bad = load_candidates([_srow(blocks=[name], **action)])
    assert good == [] and "could never be traded" in bad[0][1] and name in bad[0][1]


def test_h5c_a_long_with_a_ban_list_block_is_still_scored():
    row = _srow(side="long", stop=98.0, target_1=103.0, entry_limit=100.5, action="OVERRIDE", desk_action="SKIP",
                blocks=["ban_list"])
    (c,), bad = load_candidates([row])
    assert bad == [] and c.side is Side.LONG


def test_h5c_dedupe_keeps_a_long_and_a_short_of_the_same_symbol_and_day():
    lg = _srow(side="long", stop=98.0, target_1=103.0, entry_limit=100.5, input_hash="hl")
    sh, sh2 = _srow(), _srow(input_hash="hs2", created_at="2026-10-27T11:07:00+05:30")
    good, _ = load_candidates([lg, sh, sh2])
    assert sorted(c.side.value for c in good) == ["long", "short"]
    assert next(c for c in good if c.side is Side.SHORT).key.startswith("hs|")  # the earliest short


def test_h5c_short_cost_defaults_to_the_mirrored_side_cost():
    from nse_backtest.desk.costs import side_cost
    (c,), _ = load_candidates([{k: v for k, v in _srow().items() if k != "cost_per_share"}])
    assert c.cost_per_share == pytest.approx(side_cost(NSE, Mode.INTRADAY, 100.0, 97.0, 10, side=Side.SHORT) / 10)


def test_h5c_scoreboard_version_bumped_for_shorts():
    assert sb.SCOREBOARD_VERSION == 14  # H5c (14): shorts replayed mirrored; candidates carry their side


def test_h5c_candidate_and_stats_side_are_the_last_fields_defaulting_to_long():
    from dataclasses import fields
    assert fields(Candidate)[-1].name == "side" and fields(Candidate)[-1].default is Side.LONG
    assert fields(sb.ScoreStats)[-1].name == "side" and fields(sb.ScoreStats)[-1].default is Side.LONG


# ---------------------------------------------------------------- mirrored rules, hand cases

def test_h5c_exit_status_and_r_are_mirrored():
    c = scand(cps=0.1)
    assert sb.exit_status(c, 97.0) is Status.WON and sb.exit_status(c, 96.0) is Status.WON
    assert sb.exit_status(c, 102.0) is Status.LOST and sb.exit_status(c, 103.0) is Status.LOST
    assert sb.exit_status(c, 99.0) is Status.TIME
    assert sb.realised_r(c, 99.8, 97.0) == pytest.approx((99.8 - 97.0 - 0.1) / 2.0)


def test_h5c_sell_stop_limit_fill_and_target():
    # nothing on the containing bar; 10:15 opens inside [limit, trigger] -> fill at its open; 10:30 hits the target
    o = _s3(None, {"10:15": (99.8, 100.1, 99.6, 99.7), "10:30": (99.7, 99.9, 96.8, 97.0)})
    assert o.status is Status.WON and o.fill_price == 99.8 and o.exit_price == 97.0
    assert o.fill_at == "2026-10-27 10:15:00" and o.realised_r == pytest.approx(1.4)
    assert o.candidate.side is Side.SHORT


def test_h5c_sell_stop_limit_gap_below_the_limit_rests_and_fills_at_the_limit():
    o = _s3(None, {"10:15": (99.0, 99.3, 98.8, 99.1), "10:30": (99.1, 99.6, 99.0, 99.4),
                   "10:45": (99.4, 99.5, 96.5, 96.8)})
    assert o.fill_price == 99.5 and o.fill_at == "2026-10-27 10:30:00" and o.status is Status.WON
    never = _s3(None, {f"{h:02d}:{m:02d}": (99.0, 99.3, 98.8, 99.1) for h in range(10, 15) for m in (15, 30, 45, 0)
                       if (h, m) != (10, 0)})
    assert never.status is Status.NOT_FILLED  # triggered below the limit and never back up to it


def test_h5c_fill_bar_stop_is_lost_at_the_stop_and_a_later_gap_exits_at_the_open():
    o = _s3(None, {"10:15": (99.9, 102.2, 99.7, 101.0)})
    assert o.status is Status.LOST and o.exit_price == 102.0 and o.realised_r == pytest.approx(-1.05)
    g = _s3(None, {"10:15": (99.9, 100.0, 99.7, 99.8), "10:30": (102.6, 103.0, 102.4, 102.8)})
    assert g.status is Status.LOST and g.exit_price == 102.6  # max(Open, stop)


def test_h5c_the_bar_spanning_the_15_10_square_off_counts_only_against_the_short():
    """Squared off by 15:10, inside the 15:00 bar: its stop counts, its target never does, and with no stop the time
    exit is at its High (the worst buy-back price the bar allows before 15:10)."""
    flat = {f"{h:02d}:{m:02d}": (99.4, 99.6, 99.3, 99.5) for h in range(10, 15) for m in (0, 15, 30, 45)
            if (10, 15) < (h, m)}
    base = {"10:15": (99.9, 100.0, 99.7, 99.8), **flat}

    def run(bar15, now=NOW):
        return _s3(None, {**base, "15:00": bar15}, now=now)
    o = run((99.5, 99.9, 98.9, 99.0))
    assert o.status is Status.TIME and o.exit_price == 99.9 and o.exit_at == "2026-10-27 15:00:00"
    assert o.note == sb.SQUARE_OFF_NOTE and o.realised_r == pytest.approx((99.9 - 99.9) / 2)
    lost = run((99.5, 102.3, 98.0, 99.0))  # a stop inside the square-off bar may have come first
    assert lost.status is Status.LOST and lost.exit_price == 102.0
    gap = run((102.6, 103.0, 102.4, 102.8))
    assert gap.status is Status.LOST and gap.exit_price == 102.6
    no_target = run((99.0, 99.2, 96.0, 96.5))  # the target in the square-off bar is never credited
    assert no_target.status is Status.TIME and no_target.exit_price == 99.2
    assert run((99.5, 99.9, 98.9, 99.0), now=datetime(2026, 10, 27, 15, 5, tzinfo=IST)).status is Status.OPEN
    assert run((99.5, 102.3, 98.0, 99.0), now=datetime(2026, 10, 27, 15, 5, tzinfo=IST)).status is Status.LOST
    missing = sb.resolve_intraday(scand(), _session(base, last="14:45"), flat_by=time(15, 15), now=NOW)
    assert missing.status is Status.UNAVAILABLE and missing.note == "incomplete session bars (last bar 14:45)"
    later = _s3(None, {**base, "15:00": (99.5, 99.9, 98.9, 99.0), "15:15": (50.0, 200.0, 40.0, 60.0)})
    assert later.exit_at == "2026-10-27 15:00:00"  # nothing after the square-off bar is ever used


def test_h5c_on_the_grid_square_off_has_no_square_bar(monkeypatch):
    from nse_backtest.desk import calendars
    hold, square = sb.short_hold(NSE, date(2026, 10, 27))
    assert (hold, square) == (time(15, 15), time(15, 0))
    early = date(2026, 10, 28)
    monkeypatch.setitem(calendars.EARLY_CLOSES, "NSE", {early: time(13, 30)})  # a fictional early close
    assert sb.short_hold(NSE, early) == (time(13, 15), None)  # its flat-by is on the grid: no square-off bar


def test_h5c_a_short_logged_at_the_square_off_is_rejected_and_one_after_cancel_by_is_not_filled():
    o = _s3(None, {}, created=(14, 50))
    assert o.status is Status.NOT_FILLED and o.note == "logged outside the entry window"


def test_h5c_entry_window_mirrors_the_long():
    o = _s3(None, {"14:45": (99.9, 100.0, 99.6, 99.8)})  # triggers only on the 14:45 bar: after no_entry_after
    assert o.status is Status.NOT_FILLED and o.note == "not filled inside the entry window (cancel by 14:45)"
    for created in ((9, 20), (14, 55)):
        for cp in (99.8, 99.0):
            o = _s3(cp, {"09:30": (99.8, 99.9, 96.0, 96.5)}, created=created, quote_live=True)
            assert o.status is Status.NOT_FILLED and o.note == "logged outside the entry window", (created, cp)


def test_h5c_plain_sell_limit_fills_at_the_trusted_logged_price():
    traded = {"10:00": (99.9, 100.0, 99.7, 99.8), "10:15": (99.7, 99.9, 96.8, 97.0)}
    o = _s3(99.8, traded, quote_live=True)
    assert o.status is Status.WON and o.fill_price == 99.8 and o.realised_r == pytest.approx(1.4)
    assert o.fill_at == "2026-10-27T10:07:00+05:30" and o.note == "limit filled at the logged price"
    mine = _s3(99.8, traded, quote_live=True, record="override")
    assert mine.fill_price == 99.8 and mine.status is Status.WON
    stop = _s3(99.8, {"10:00": (99.9, 102.5, 99.7, 99.8)}, quote_live=True)  # a containing-bar stop is kept
    assert stop.status is Status.LOST and stop.exit_price == 102.0


def test_h5c_trust_band_is_mirrored():
    """A live sell price is trusted inside [Low - 0.5%, High] of its bar: the tolerance is below the Low only."""
    near = _s3(99.8, {"10:00": (100.0, 100.3, 99.9, 100.1), "10:15": (100.0, 100.05, 96.8, 97.0)}, quote_live=True)
    assert near.fill_price == 99.8  # 0.1 under the Low, within 0.5%
    above = _s3(99.8, {"10:00": (99.5, 99.7, 99.4, 99.6)}, quote_live=True)  # over the High: contradiction
    assert above.status is Status.UNAVAILABLE
    assert above.note == "logged live price 99.8 is outside the bar's range 99.4-99.7"
    far = _s3(99.8, {"10:00": (100.6, 100.9, 100.4, 100.8)}, quote_live=True)  # 0.6 under the Low: beyond 0.5%
    assert far.status is Status.UNAVAILABLE and far.note.endswith("100.4-100.9")


@pytest.mark.parametrize("live", [False, None])
def test_h5c_untrusted_plain_price_uses_the_resting_model(live):
    down = {f"{h:02d}:{m:02d}": (99.0, 99.1, 96.8, 97.0) for h in range(10, 15) for m in (0, 15, 30, 45)
            if (h, m) != (10, 0)}
    o = _s3(99.8, {"10:00": (99.9, 100.0, 99.0, 99.1), **down}, quote_live=live)
    assert o.status is Status.NOT_FILLED and o.note == AMBIG  # the containing-bar fill would win: not counted


def test_h5c_resting_sell_limit_fills_at_the_limit_never_better():
    lo = (99.0, 99.3, 98.8, 99.1)  # below the limit: no fill
    o = _s3(99.0, {"10:00": lo, "10:15": lo, "10:30": (99.4, 99.8, 99.2, 99.6), "10:45": (99.6, 99.7, 96.8, 97.0)},
            quote_live=True)
    assert o.status is Status.WON and o.fill_price == 99.5 and o.fill_at == "2026-10-27 10:30:00"
    assert o.note == "resting limit filled at the limit"
    cont = _s3(99.0, {"10:00": (99.2, 102.3, 99.1, 99.3)}, quote_live=True)  # containing-bar fill and stop
    assert cont.status is Status.LOST and cont.fill_price == 99.5 and cont.exit_price == 102.0


def test_h5c_ambiguous_containing_bar_fill_counts_only_if_it_loses_with_the_cap():
    # sell stop-limit, logged above the trigger; the containing bar straddles the trigger and nothing later fills
    down = {f"{h:02d}:{m:02d}": (99.3, 99.4, 96.8, 97.0) for h in range(10, 15) for m in (0, 15, 30, 45)
            if (h, m) != (10, 0)}
    win = _s3(100.2, {"10:00": (99.8, 100.5, 99.7, 100.2), **down}, quote_live=True)
    assert win.status is Status.NOT_FILLED and win.note == AMBIG
    lose = _s3(100.2, {"10:00": (99.8, 102.5, 99.7, 100.2)}, quote_live=True)
    assert lose.status is Status.LOST and lose.fill_at == "2026-10-27T10:07:00+05:30" and lose.fill_price == 100.0
    # the containing-bar fill would win; a later real fill better than it is capped to NOT_FILLED
    gap = {"10:15": (98.0, 98.1, 97.0, 97.5), "10:30": (99.0, 99.6, 98.8, 99.1), "10:45": (94.0, 94.2, 93.5, 93.8)}
    capped = _s3(100.2, {"10:00": (99.8, 100.5, 99.7, 100.2), **gap}, quote_live=True)
    assert capped.status is Status.NOT_FILLED and capped.note == AMBIG
    # a later fill no better than the containing-bar one is counted
    worse = _s3(100.2, {"10:00": (99.8, 100.5, 99.7, 100.2), "10:15": (98.0, 98.1, 97.0, 97.5),
                        "10:30": (99.0, 99.6, 98.8, 99.1)}, quote_live=True)
    assert worse.status is Status.TIME and worse.fill_at == "2026-10-27 10:30:00" and worse.realised_r < 0


def test_h5c_no_price_fills_at_the_worst_consistent_price():
    certain = _s3(None, {"10:00": (99.7, 99.8, 99.55, 99.6), "10:15": (100.4, 100.5, 99.9, 100.0),
                         "10:30": (99.9, 100.0, 96.8, 97.0)})
    assert certain.status is Status.WON and certain.fill_price == 99.5 and certain.note == sb.CERTAIN_FILL_NOTE
    straddle = _s3(None, {"10:00": (99.8, 102.5, 99.7, 100.2)})
    assert straddle.status is Status.LOST and straddle.fill_price == 99.7  # max(Low, limit): the worst sell price


def test_h5c_gaps_are_unavailable():
    bars = _session({"10:15": (99.9, 100.0, 99.7, 99.8)}).drop(pd.Timestamp("2026-10-27 12:00"))
    o = sb.resolve_intraday(scand(), bars, flat_by=time(15, 15), now=NOW)
    assert o.status is Status.UNAVAILABLE and o.note == "gap in intraday bars (missing after 11:45)"
    late = _session({}, first="10:15")
    o = sb.resolve_intraday(scand(), late, flat_by=time(15, 15), now=NOW)
    assert o.status is Status.UNAVAILABLE and "no bar containing 10:07" in o.note
    short_day = _session({"10:15": (99.9, 100.0, 99.7, 99.8)}, last="13:00")
    o = sb.resolve_intraday(scand(), short_day, flat_by=time(15, 15), now=NOW)
    assert o.status is Status.UNAVAILABLE and o.note == "incomplete session bars (last bar 13:00)"


def test_h5c_a_late_quote_moves_the_start_of_the_replay():
    """Mirror of r9b: TAKE stamped 10:07, quote seen at 10:17 (99.4, under the limit: a resting sell limit). The
    10:00 bar filled it but came before the quote; the 10:15 bar is the one the order was logged in (ambiguous)."""
    below = {f"{t:%H:%M}": (99.0, 99.1, 96.8, 97.0) for t in pd.date_range("2026-10-27 10:30", "2026-10-27 14:45",
                                                                           freq="15min")}
    over = {"10:00": (99.3, 99.4, 99.1, 99.2), "10:15": (99.4, 99.55, 99.3, 99.4), **below}
    late = _s3(99.4, over, quote_live=True, quote_as_of=datetime(2026, 10, 27, 10, 17, tzinfo=IST))
    assert late.status is Status.NOT_FILLED and late.note == AMBIG
    assert late.candidate.created_at == datetime(2026, 10, 27, 10, 7, tzinfo=IST)  # the logged row is unchanged
    at_take = _s3(99.4, {**over, "10:00": (99.4, 99.45, 99.3, 99.4)}, quote_live=True)  # quote at the TAKE
    assert at_take.status is Status.WON and at_take.fill_at == "2026-10-27 10:15:00" and at_take.fill_price == 99.5


def test_h5c_swing_short_cannot_be_resolved():
    c = replace(scand(), mode=Mode.SWING)
    with pytest.raises(ValueError, match="intraday only"):
        resolve_swing(c, pd.DataFrame(), fill_session=date(2026, 10, 27), now=NOW)


def test_h5c_summarize_never_pools_longs_and_shorts():
    def out(side, r):
        c = Candidate(datetime(2026, 10, 27, 10, 7, tzinfo=IST), "NSE", "ABC", Mode.INTRADAY, 100.0,
                      98.0 if side is Side.LONG else 102.0, 103.0 if side is Side.LONG else 97.0, 26, 0.0, 10, 0.1,
                      None, f"{side.value}{r}", side=side)
        return sb.Outcome(c, Status.WON if r > 0 else Status.LOST, 100.0, "x", 101.0, "y", r, None, 1, "")
    stats = summarize([out(Side.SHORT, -1.0), out(Side.LONG, 1.0), out(Side.LONG, 2.0)])
    assert [(s.side, s.n, s.mean_r) for s in stats] == [(Side.LONG, 2, 1.5), (Side.SHORT, 1, -1.0)]


# ---------------------------------------------------------------- I/O: cache, build, CLI
import json  # noqa: E402

from nse_backtest.desk import scoreboard_io  # noqa: E402

IO_NOW = datetime(2026, 10, 29, 12, 0, tzinfo=IST)


def _io_rows():
    base = {"created_at": "2026-10-27T10:07:00+05:30", "action": "TAKE", "symbol": "ABC", "market": "NSE",
            "mode": "intraday", "qty": 10, "mc_expectancy_r": 0.2, "history_expectancy_r": 0.1,
            "max_hold_bars": 20, "cost_per_share": 0.0, "last_bar": "2026-10-27T10:00:00", "last_close": None}
    long_ = {**base, "input_hash": "hl", "entry": 101.0, "stop": 99.0, "target_1": 104.0, "entry_limit": 101.5}
    short = {**base, "input_hash": "hs", "entry": 100.0, "stop": 102.0, "target_1": 97.0, "entry_limit": 99.5,
             "side": "short"}
    return long_, short


def _io_bars():
    # 10:15 opens above the sell trigger (100) and dips through it to 99.7, 10:30 reaches 96.8; the long trigger
    # (101) is never reached
    return _session({"10:15": (100.1, 100.15, 99.7, 99.8), "10:30": (99.8, 99.9, 96.8, 97.0)},
                    idle=(100.2, 100.4, 100.1, 100.3))


class _Loader:
    def __init__(self, frame):
        self.frame, self.calls = frame, 0

    def __call__(self, profile, symbol, *a, **k):
        self.calls += 1
        return self.frame


def _build(tmp_path, rows, loader):
    audit = tmp_path / "desk_audit.jsonl"
    audit.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return scoreboard_io.build_scoreboard("NSE", now=IO_NOW, audit_path=audit, cache_path=tmp_path / "c.json",
                                          on_warning=None, load_daily=None, load_intraday=loader)


def test_h5c_build_scores_a_short_beside_a_long_and_caches_it_with_its_side(tmp_path):
    loader = _Loader(_io_bars())
    res = _build(tmp_path, _io_rows(), loader)
    assert res.rejects == [] and loader.calls == 1  # one symbol: the bars are loaded once for both sides
    by = {o.candidate.side: o for o in res.outcomes}
    assert by[Side.LONG].status is Status.NOT_FILLED
    s = by[Side.SHORT]
    assert s.status is Status.WON and s.fill_price == 100.0 and s.exit_price == 97.0
    assert s.fill_at == "2026-10-27 10:15:00" and s.realised_r == pytest.approx(1.5)
    assert [(st.side, st.n) for st in res.stats] == [(Side.LONG, 0), (Side.SHORT, 1)]
    cache = json.loads((tmp_path / "c.json").read_text())
    assert cache["version"] == 14
    assert sorted(e["candidate"]["side"] for e in cache["outcomes"].values()) == ["long", "short"]
    again = _build(tmp_path, _io_rows(), loader)  # served from the cache
    assert loader.calls == 1 and [o.status for o in again.outcomes] == [o.status for o in res.outcomes]
    assert {o.candidate.side: o for o in again.outcomes}[Side.SHORT].realised_r == pytest.approx(1.5)


def test_h5c_a_cached_entry_of_the_other_side_is_never_served(tmp_path):
    loader = _Loader(_io_bars())
    _, short = _io_rows()
    _build(tmp_path, [short], loader)
    path = tmp_path / "c.json"
    data = json.loads(path.read_text())
    (entry,) = data["outcomes"].values()
    entry["candidate"]["side"] = "long"
    path.write_text(json.dumps(data))
    res = _build(tmp_path, [short], loader)
    assert loader.calls == 2 and res.outcomes[0].status is Status.WON  # recomputed, not served


def test_h5c_cached_short_results_are_checked_with_the_mirrored_rules():
    c = scand()
    o = sb.resolve_intraday(c, _io_bars(), flat_by=time(15, 15), now=NOW)
    entry = scoreboard_io._entry(o)
    assert scoreboard_io._outcome_from(c, entry) == o
    bad = json.loads(json.dumps(entry))
    bad["outcome"]["status"] = "lost"  # a status its own prices contradict
    assert scoreboard_io._outcome_from(c, bad) is None


def test_h5c_cli_labels_short_stats_and_trades(tmp_path):
    from nse_backtest.desk.cli import format_scoreboard
    res = _build(tmp_path, _io_rows(), _Loader(_io_bars()))
    text = "\n".join(format_scoreboard(res, "NSE"))
    # F2/M12: one mode label for the CLI and the page (models.mode_label): "intraday SHORT"
    assert "NSE intraday: " in text and "NSE intraday SHORT: " in text
    assert any(" intraday SHORT " in ln and "won" in ln for ln in text.splitlines())
    assert "Projected +0.20R vs realised +1.50R over 1 trade" in text  # the short group's own projections


def test_h5c_page_shows_short_stats_apart_from_longs(monkeypatch, tmp_path):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    from nse_backtest.desk import session
    from tests.test_desk_page_smoke import _patch_ok

    def out(side, r, mc):
        c = Candidate(datetime(2026, 10, 27, 10, 7, tzinfo=IST), "NSE", "ABC", Mode.INTRADAY, 100.0,
                      98.0 if side is Side.LONG else 102.0, 103.0 if side is Side.LONG else 97.0, 26, 0.0, 10, mc,
                      None, f"{side.value}{r}", side=side)
        return sb.Outcome(c, Status.WON if r > 0 else Status.LOST, 100.0, "x", 101.0, "y", r, None, 1, "")
    outs = [out(Side.LONG, 1.0, 0.1), out(Side.SHORT, -1.0, 0.2)]
    monkeypatch.setattr(session, "run_scoreboard",
                        lambda market=None, **k: session.ScoreboardResult(outs, summarize(outs), [], None))
    _patch_ok(monkeypatch, tmp_path)
    at = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "pages/0_Desk.py"), default_timeout=120).run()
    at.button(key="desk_load_scoreboard").click().run()
    assert not at.exception
    kids = list(at.tabs[5].children.values())
    assert sum(1 for e in kids if e.type == "plotly_chart") == 2  # one projection chart per side
    assert any("**NSE intraday short**" in i.value for i in at.info)
    assert any("Projected vs realised R — intraday short" in m.value for m in at.markdown)
    stats = at.dataframe[0].value
    assert list(stats["Mode"]) == ["intraday", "intraday short"]
    assert "intraday short" in list(at.dataframe[1].value["Mode"])


# ---------------------------------------------------------------- mirrored property tests (2,000 seeded paths each)
PATHS = 2000
DAY_IDX = pd.date_range("2026-10-27 09:15", "2026-10-27 15:00", freq="15min")
HOLD_LAST = pd.Timestamp("2026-10-27 15:00")  # the bar that spans the 15:10 square-off: the last one ever used


def _short_day(rng):
    """A random session around the sell trigger: wide and narrow bars, gaps through the stop and the target."""
    spread = float(rng.choice([0.4, 1.0, 2.5]))
    return _frame(rng, DAY_IDX, float(rng.uniform(99.0, 101.0)), spread)


def _start(c):
    return max(c.created_at, c.quote_as_of) if c.quote_as_of is not None else c.created_at


def _in_quote_band(c, b):
    qb = pd.Timestamp(c.quote_as_of.astimezone(IST).replace(tzinfo=None)).floor("15min")
    if qb not in b.index:
        return None
    lo, hi = float(b.loc[qb, "Low"]), float(b.loc[qb, "High"])
    return lo - 0.005 * c.current_price <= c.current_price <= hi


def _case(rng, branch, n):
    """(candidate, bars) for one seeded path of ``branch``."""
    hm = divmod(int(rng.integers(9 * 60 + 16, 14 * 60 + 44)), 60)
    sec = int(rng.integers(0, 60))
    record = ["desk", "override"][n % 2] if branch != "override" else "override"
    cps = float(rng.choice([0.0, 0.05, 0.2]))
    b = _short_day(rng)
    cp, ql = None, None
    if branch == "stop_limit":
        cp = [None, 100.2, 100.6, 101.5][n % 4]
        ql = [True, False, None][int(rng.integers(3))] if cp is not None else None
    elif branch == "plain":
        cp, ql = float(rng.choice([99.5, 99.6, 99.75, 99.9, 100.0])), True
    elif branch == "resting":
        cp, ql = float(rng.choice([98.0, 99.0, 99.3, 99.45])), [True, False, None][n % 3]
    elif branch == "override":
        cp = [None, 100.3, 99.8, 99.5, 99.2][n % 5]
        ql = [True, False][n % 2] if cp is not None else None
    elif branch == "untrusted":
        cp, ql = float(rng.choice([99.5, 99.7, 99.9, 100.0])), [False, None][n % 2]
    at = datetime(2026, 10, 27, *hm, sec, tzinfo=IST)
    qa = at + timedelta(minutes=int(rng.integers(-3, 9))) if ql is True else None
    c = Candidate(at, "NSE", "ABC", Mode.INTRADAY, E, SL, T1, 26, cps, 10, None, None, f"p{n}", entry_limit=LIM,
                  record=record, current_price=cp, quote_live=ql, quote_as_of=qa, side=Side.SHORT)
    if ql is True and branch in ("plain", "override") and n % 3:  # make most live prices ones the bar traded at
        qb = pd.Timestamp(qa.astimezone(IST).replace(tzinfo=None)).floor("15min")
        if qb in b.index:
            h_, l_ = float(b.loc[qb, "High"]), float(b.loc[qb, "Low"])
            b.loc[qb, ["High", "Low"]] = [max(h_, cp), min(l_, cp)]
    return c, b


def _check_short_invariants(c, o, b):
    """Every scoreboard invariant, mirrored for a sell."""
    st = o.status
    assert o.candidate is c
    if st in F:
        assert c.entry_limit - 1e-9 <= o.fill_price <= c.entry + 1e-9  # a sell fills inside [limit, trigger]
        r = (o.fill_price - o.exit_price - c.cost_per_share) / (c.stop - c.entry)
        assert o.realised_r == pytest.approx(r, abs=1e-12)
        assert pd.Timestamp(o.exit_at) <= HOLD_LAST  # nothing after the square-off is ever used
        if c.current_price is not None and o.note == "limit filled at the logged price":
            assert o.fill_price == c.current_price and c.quote_live is True  # only a trusted price fills there
        fa = o.fill_at
        if fa != _start(c).isoformat():  # a later bar: inside the entry window
            t = pd.Timestamp(fa).time()
            assert NSE.no_entry_before <= t <= NSE.no_entry_after
    if o.fill_price is not None:  # a stop touched on the fill bar is a loss at the stop; its target never counts
        fb = pd.Timestamp(pd.Timestamp(o.fill_at).tz_localize(None) if pd.Timestamp(o.fill_at).tzinfo is None
                          else pd.Timestamp(o.fill_at).tz_convert(IST).tz_localize(None)).floor("15min")
        if float(b.loc[fb, "High"]) >= c.stop:
            assert st is Status.LOST and o.exit_price == c.stop and pd.Timestamp(o.exit_at) == fb, o
    if st is Status.LOST:
        assert o.exit_price >= c.stop - 1e-9 and o.exit_price >= o.fill_price - 1e-9
        assert o.realised_r <= 1e-12  # LOST => R <= 0
        assert o.realised_r + c.cost_per_share / (c.stop - c.entry) <= 1e-9  # no gain before costs
    if st is Status.WON:
        assert o.exit_price <= c.target_1 + 1e-9  # WON => exit at or beyond (below) the target
    if st in F and pd.Timestamp(o.exit_at) == HOLD_LAST:  # the square-off bar: never a win, its stop never missed
        assert st is not Status.WON
        if float(b.loc[HOLD_LAST, "High"]) >= c.stop:
            assert st is Status.LOST
    if st is Status.TIME:  # squared off inside the 15:00 bar at its worst price (its High), never its Close
        assert c.target_1 < o.exit_price < c.stop and o.note == sb.SQUARE_OFF_NOTE
        assert pd.Timestamp(o.exit_at) == HOLD_LAST and o.exit_price == float(b.loc[HOLD_LAST, "High"])
    if st is Status.OPEN:
        assert o.realised_r is None


def _containing_path(c, b):
    real = sb._only_if_it_loses
    try:
        sb._only_if_it_loses = lambda c_, o_: o_  # the containing-bar outcome as computed: no fallback
        return sb.resolve_intraday(c, b, flat_by=time(15, 15), now=NOW)
    finally:
        sb._only_if_it_loses = real


def _certain(c, b):
    """A fill the containing bar made certain: no trusted price and the whole bar inside [limit, trigger]."""
    bar0 = pd.Timestamp(_start(c).astimezone(IST).replace(tzinfo=None)).floor("15min")
    lo, hi = float(b.loc[bar0, "Low"]), float(b.loc[bar0, "High"])
    return hi <= c.entry and lo >= c.entry_limit


@pytest.mark.parametrize("branch", ["stop_limit", "plain", "resting", "override", "untrusted"])
def test_h5c_property_every_invariant_holds_mirrored(branch):
    rng = np.random.default_rng(1900 + ["stop_limit", "plain", "resting", "override", "untrusted"].index(branch))
    seen, n_cap = set(), 0
    for n in range(PATHS):
        c, b = _case(rng, branch, n)
        o = sb.resolve_intraday(c, b, flat_by=time(15, 15), now=NOW)
        _check_short_invariants(c, o, b)
        seen.add(o.status)
        trusted = c.quote_live is True and c.current_price is not None and _in_quote_band(c, b)
        if c.quote_live is True and c.current_price is not None and _in_quote_band(c, b) is False:
            assert o.status is Status.UNAVAILABLE or o.note == "logged outside the entry window"
        if trusted:  # a live price inside [Low - 0.5%, High] of its bar is never a contradiction
            assert not o.note.startswith("logged live price"), (n, o)
        plain = trusted and c.entry_limit <= c.current_price <= c.entry
        ticket_stop_limit = c.current_price is None or c.current_price > c.entry
        # a fill only the containing bar made possible never wins (unless it was certain or a trusted plain fill)
        if o.fill_at == _start(c).isoformat() and o.realised_r is not None and not plain:
            certain = ticket_stop_limit and not trusted and _certain(c, b)
            assert o.realised_r <= 1e-12 or certain, (n, o)
            if certain:
                assert o.fill_price == c.entry_limit  # the worst price in the bar
        # the cap: a counted result is never better than the containing-bar path
        cont = _containing_path(c, b)
        if cont.fill_at == _start(c).isoformat() and cont.realised_r is not None and o.realised_r is not None:
            n_cap += 1
            assert o.realised_r <= cont.realised_r + 1e-12, (n, cont, o)
        # a later-bar stop-limit fill is exactly fills.stop_limit_fill_short over the entry window
        stop_limit = c.current_price is None or c.current_price > c.entry  # the ticket printed a sell stop-limit
        if stop_limit and o.fill_price is not None and o.fill_at != _start(c).isoformat():
            bar0 = pd.Timestamp(_start(c).astimezone(IST).replace(tzinfo=None)).floor("15min")
            later = b[(b.index > bar0)]
            win = [i for i, t in enumerate(later.index) if NSE.no_entry_before <= t.time() <= NSE.no_entry_after]
            got = stop_limit_fill_short(later["Open"], later["High"], later["Low"], win[0], win[-1], E, LIM)
            assert got is not None and later.index[got[0]] == pd.Timestamp(o.fill_at) and got[1] == o.fill_price
    assert {Status.WON, Status.LOST} <= seen, seen  # the paths really reach wins and losses
    assert n_cap > 50


def test_h5c_property_gaps_are_unavailable():
    rng = np.random.default_rng(1907)
    for n in range(PATHS):
        c, b = _case(rng, ["stop_limit", "plain", "resting", "override"][n % 4], n)
        bar0 = pd.Timestamp(_start(c).astimezone(IST).replace(tzinfo=None)).floor("15min")
        inside = [t for t in b.index if bar0 < t < HOLD_LAST]
        if not inside:
            continue
        gappy = b.drop(inside[int(rng.integers(len(inside)))])
        o = sb.resolve_intraday(c, gappy, flat_by=time(15, 15), now=NOW)
        assert o.status is Status.UNAVAILABLE or o.note == "logged outside the entry window", (n, o)


def test_h5c_property_short_is_the_exact_mirror_of_the_long():
    """The short on bars reflected about 100 (p -> 200 - p) gives the long replay's result on the original bars: same
    status, note, times and R, prices reflected back (the long given the same 15:00 square-off bar). Cases where the
    two trust bands disagree (the 0.5% tolerance is relative to the price) are skipped."""
    rng = np.random.default_rng(1908)
    compared = 0
    for n in range(PATHS):
        cs, bs = _case(rng, ["stop_limit", "plain", "resting", "override", "untrusted"][n % 5], n)
        lb = pd.DataFrame({"Open": 200 - bs["Open"], "High": 200 - bs["Low"], "Low": 200 - bs["High"],
                           "Close": 200 - bs["Close"]}, index=bs.index)
        cl = replace(cs, stop=98.0, target_1=103.0, entry_limit=100.5, side=Side.LONG,
                     current_price=None if cs.current_price is None else 200 - cs.current_price)
        if cs.quote_live is True and cs.current_price is not None:
            if _in_quote_band(cs, bs) != sb._price_trust(cl, lb)[0].startswith("trusted"):
                continue
        s = sb.resolve_intraday(cs, bs, flat_by=time(15, 15), now=NOW)
        lg = sb._as_logged(cl, sb._resolve_intraday_at(sb._from_quote(cl), lb, flat_by=time(15, 15), now=NOW,
                                                       square=time(15, 0)))
        if s.status is Status.UNAVAILABLE and s.note.startswith("logged live price"):
            assert lg.status is Status.UNAVAILABLE and lg.note.startswith("logged live price")
            compared += 1
            continue
        compared += 1
        assert (s.status, s.note, s.fill_at, s.exit_at, s.bars_held) == \
            (lg.status, lg.note, lg.fill_at, lg.exit_at, lg.bars_held), n
        for a, bb in ((s.fill_price, lg.fill_price), (s.exit_price, lg.exit_price)):
            assert (a is None and bb is None) or a == pytest.approx(200 - bb, abs=1e-9)
        for a, bb in ((s.realised_r, lg.realised_r), (s.mtm_r, lg.mtm_r)):
            assert (a is None and bb is None) or a == pytest.approx(bb, abs=1e-9)
    assert compared > 0.9 * PATHS


# ---------------------------------------------------------------- H5c minors
def test_h5c_off_grid_early_close_keeps_the_square_off_bar(monkeypatch):
    """A fictional 14:05 close: flat-by 13:50, so the short squares off inside the 13:45 bar; its target never
    counts and a time exit is at its High."""
    from nse_backtest.desk import calendars
    monkeypatch.setitem(calendars.EARLY_CLOSES, "NSE", {date(2026, 10, 27): time(14, 5)})
    flat = calendars.flat_by_for(NSE, date(2026, 10, 27))
    assert flat == time(13, 50) and sb.short_hold(NSE, date(2026, 10, 27)) == (time(14, 0), time(13, 45))
    assert sb.short_hold(NSE, date(2026, 10, 27), time(13, 30)) == (time(13, 30), None)  # an earlier caller cap
    base = {"10:15": (99.9, 100.0, 99.7, 99.8)}
    bars = _session({**base, "13:45": (99.0, 99.2, 96.0, 96.5)}, last="13:45")
    o = sb.resolve_intraday(scand(), bars, flat_by=flat, now=NOW)
    assert o.status is Status.TIME and o.exit_price == 99.2 and o.exit_at == "2026-10-27 13:45:00"
    assert o.note == sb.SQUARE_OFF_NOTE
    stop = sb.resolve_intraday(scand(), _session({**base, "13:45": (99.0, 102.4, 98.0, 99.0)}, last="13:45"),
                               flat_by=flat, now=NOW)
    assert stop.status is Status.LOST and stop.exit_price == 102.0


@pytest.mark.parametrize("blocks", ["short_fno", {"ban_list": 1}, 7])
def test_h5c_unreadable_blocks_field_fails_closed(blocks):
    good, bad = load_candidates([_srow(blocks=blocks)])
    assert good == [] and "unreadable blocks field" in bad[0][1]


def test_h5c_cli_mode_column_fits_intraday_short(tmp_path):
    from nse_backtest.desk.cli import format_scoreboard
    lines = format_scoreboard(_build(tmp_path, _io_rows(), _Loader(_io_bars())), "NSE")
    head = next(ln for ln in lines if ln.lstrip().startswith("date"))
    rows = [ln for ln in lines if ln.startswith("  2026-10-27")]
    col = head.index("status")
    assert all(r[col - 1] == " " and r[col] != " " for r in rows)  # every status starts under its header
