import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from nse_backtest.desk.audit import append_verdict
from nse_backtest.desk.cli import to_jsonable
from nse_backtest.desk.markets import NSE
from nse_backtest.desk.models import Action, Check, DeskVerdict, Limits, Mode, Odds, Sizing, Status, TradePlan
from nse_backtest.desk.projections import account_projection, outcome_money, price_band, projected_vs_real
from nse_backtest.desk.scoreboard import Candidate, Outcome
from nse_backtest.desk.scoreboard import Status as OStatus
from nse_backtest.desk.timemachine import MC_RS_CAP, monte_carlo, monte_carlo_rs, odds
from nse_backtest.desk.tradecard import build_card
from nse_backtest.desk.verdict import input_hash

from tests.desk.test_timemachine import geometric, plan


def fixed_ratios():
    g = np.random.default_rng(7)
    c, o = 1 + g.normal(0.0005, 0.015, 120), 1 + g.normal(0, 0.004, 120)
    h = np.maximum(o, c) * (1 + np.abs(g.normal(0, 0.006, 120)))
    lo = np.minimum(o, c) * (1 - np.abs(g.normal(0, 0.006, 120)))
    return np.column_stack([o, h, lo, c])


MC_ARGS = (100.0, 97.0, 104.5, 15)
MC_KW = dict(cost_per_share=0.2, replays=3000, seed=19)


# ---------------------------------------------------------------- monte carlo R array

def test_monte_carlo_summary_unchanged_by_refactor():
    # Values recorded from monte_carlo before monte_carlo_rs existed (same seed, same ratios).
    assert monte_carlo(fixed_ratios(), *MC_ARGS, **MC_KW) == (
        0.177, 0.6973333333333334, 0.12566666666666668, -0.475185923531438)


def test_monte_carlo_rs_matches_summary():
    *summary, rs = monte_carlo_rs(fixed_ratios(), *MC_ARGS, **MC_KW)
    assert tuple(summary) == monte_carlo(fixed_ratios(), *MC_ARGS, **MC_KW)
    assert rs.shape == (3000,) and float(rs.mean()) == summary[3]
    # a target hit nets (T1 - entry - cost) / risk: the best possible replay, gaps up aside
    assert (np.isclose(rs, (104.5 - 100.0 - 0.2) / 3)).mean() == pytest.approx(summary[0], abs=0.02)


def test_odds_mc_rs_uncapped_mean_is_exact():
    o = odds(geometric(), plan(t1=104.2, stop=97.2), replays=500, robustness=False)
    assert len(o.mc_rs) == 500 and float(np.mean(o.mc_rs)) == o.mc_expectancy_r


def test_odds_mc_rs_capped_at_10000():
    o = odds(geometric(), plan(t1=104.2, stop=97.2), replays=MC_RS_CAP + 500, robustness=False)
    assert MC_RS_CAP == 10_000 and len(o.mc_rs) == MC_RS_CAP
    assert float(np.mean(o.mc_rs)) == pytest.approx(o.mc_expectancy_r, abs=0.05)


def test_mc_rs_is_kept_out_of_json_and_hash():
    o = Odds(3, 19, .5, .3, .2, .4, 0, None, None, (), None, None, None, None, None, None, None)
    big = replace(o, mc_rs=(1.0, -1.0, 0.5))
    assert "mc_rs" not in to_jsonable(big) and to_jsonable(big) == to_jsonable(o)
    assert json.dumps(to_jsonable(big))  # still serialisable
    assert input_hash(PLAN, SIZING, big, OK, 100.0) == input_hash(PLAN, SIZING, o, OK, 100.0)


def test_mc_rs_is_kept_out_of_the_audit_row(tmp_path):
    v = DeskVerdict(Action.TAKE, "h", PLAN, SIZING, replace(ODDS, mc_rs=(0.5,) * 5000), OK, (), None, 100.0,
                    "x" * 64, "2026-10-29T10:00:00")
    line = append_verdict(v, tmp_path / "a.jsonl").read_text()
    assert "mc_rs" not in line and len(line) < 2000


# ---------------------------------------------------------------- outcome money

PLAN = TradePlan("ABC", "NSE", Mode.SWING, 100, 95, 110, 115, 2, 15, 100, "2026-10-28T00:00:00", "user")
SIZING = Sizing(10, "risk", 50.0, 1_000.0, 5.0, False, "10 shares")
RS = (2.0, -1.0, -1.0, 0.3, 2.0, -1.05)
ODDS = Odds(6, 19, 0.4, 0.4, 0.2, float(np.mean(RS)), 0, None, None, (), None, None, None, None, None, None, None,
            mc_rs=RS)
OK = (Check("regime", Status.PASS, ""),)


def card(odds=ODDS, sizing=SIZING):
    v = DeskVerdict(Action.TAKE, "h", PLAN, sizing, odds, OK, (), None, 100.0, "x" * 64, "2026-10-29T10:00:00")
    return build_card(v, NSE, Limits(capital=10_000))


def test_outcome_money_percentiles():
    m = outcome_money(card())
    pnl = np.array(RS) * 10 * 5
    assert m.pnl == pytest.approx(tuple(pnl))
    assert (m.p5, m.p25, m.median, m.p75, m.p95) == pytest.approx(tuple(np.percentile(pnl, [5, 25, 50, 75, 95])))
    assert (m.p_target, m.p_stop, m.p_time) == (0.4, 0.4, 0.2)


def test_outcome_money_none_without_odds_rs_or_shares():
    assert outcome_money(card(odds=None)) is None
    assert outcome_money(card(odds=replace(ODDS, mc_rs=()))) is None
    assert outcome_money(card(sizing=replace(SIZING, qty=0, note="too small"))) is None


# ---------------------------------------------------------------- price band

def closes(n=200, seed=3):
    g = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(g.normal(0.0005, 0.02, n))), index=pd.bdate_range("2026-01-01", periods=n))


def test_price_band_shape_order_and_determinism():
    s = closes()
    b = price_band(s, 15, paths=300)
    assert b.start == s.index[-1] and b.steps == 15
    bands = (b.p5, b.p25, b.p50, b.p75, b.p95)
    assert all(len(x) == 15 for x in bands)
    arr = np.array(bands)
    assert (np.diff(arr, axis=0) >= 0).all()
    assert b.p95[-1] > b.p5[-1] and b.p95[-1] - b.p5[-1] > b.p95[0] - b.p5[0]  # widens with time
    assert price_band(s, 15, paths=300) == b
    assert price_band(s, 15, paths=300, seed=20) != b


def test_price_band_none_for_short_or_bad_history():
    assert price_band(closes(29), 10) is None
    bad = closes(); bad.iloc[50] = -1.0
    assert price_band(bad, 10) is None
    with pytest.raises(ValueError):
        price_band(closes(), 0)


# ---------------------------------------------------------------- account projection

def test_account_projection_equal_weight():
    a = account_projection(10_000, 100, [2.0, -1.0], 40, paths=2000)
    assert a.n_trades == 40 and a.start_capital == 10_000 and a.source == "Monte Carlo replays"
    assert len(a.p5) == len(a.p50) == len(a.p95) == 41
    assert a.p5[0] == a.p50[0] == a.p95[0] == 10_000
    assert a.p50[-1] == pytest.approx(10_000 + 40 * 0.5 * 100, abs=300)
    assert all(lo <= mid <= hi for lo, mid, hi in zip(a.p5, a.p50, a.p95))
    assert account_projection(10_000, 100, [2.0, -1.0], 40, paths=2000) == a


def test_account_projection_probabilities_match_paths():
    rs = [2.0, -1.0, -1.0]
    a = account_projection(5_000, 200, rs, 10, paths=500, seed=19)
    g = np.random.default_rng(19)
    draws = g.choice(np.array(rs), size=(500, 10), replace=True) * 200
    eq = 5_000 + np.concatenate([np.zeros((500, 1)), np.cumsum(draws, axis=1)], axis=1)
    assert a.p_finish_down == pytest.approx(float((eq[:, -1] < 5_000).mean()))
    dd = (np.maximum.accumulate(eq, axis=1) - eq).max(axis=1)
    assert a.p_dd_10 == pytest.approx(float((dd >= 500).mean()))
    assert 0 < a.p_finish_down < 1


def test_account_projection_none_and_source():
    assert account_projection(10_000, 100, [], 20) is None
    assert account_projection(0, 100, [1.0], 20) is None
    assert account_projection(10_000, 0, [1.0], 20) is None
    assert account_projection(10_000, 100, (1.0,), 5, paths=10, source="setup history").source == "setup history"


# ---------------------------------------------------------------- projected vs real

IST = timezone(timedelta(hours=5, minutes=30))


def outcome(i, mc, realised, status=OStatus.WON):
    c = Candidate(datetime(2026, 10, 1, tzinfo=IST) + timedelta(days=i), "NSE", "S", Mode.SWING, 100.0, 98.0, 103.0,
                  15, 0.0, 10, mc, None, f"h{i}")
    return Outcome(c, status, None, None, None, None, realised, None, 0, "")


def test_projected_vs_real_skips_and_counts_unpredicted():
    outs = [outcome(2, 0.2, -1.0, OStatus.LOST), outcome(0, 0.3, 1.5), outcome(1, None, 0.5),
            outcome(3, 0.1, None, OStatus.OPEN), outcome(4, 0.4, 0.2, OStatus.TIME)]
    proj, real, pairs, missing = projected_vs_real(outs)
    assert proj == pytest.approx([0.3, 0.5, 0.9])
    assert real == pytest.approx([1.5, 2.0, 1.0, 1.2])
    assert pairs == [(0.3, 1.5), (0.2, -1.0), (0.4, 0.2)]
    assert missing == 1
    assert projected_vs_real([]) == ([], [], [], 0)
