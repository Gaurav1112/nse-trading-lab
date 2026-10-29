"""F2 final-review fixes in the desk engine (fictional capital only). No network."""
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from nse_backtest.desk.checks import CheckContext
from nse_backtest.desk.markets import NSE
from nse_backtest.desk.models import Action, Limits, LiveQuote, Mode, ShortEdge, Side, Status
from nse_backtest.desk.nse_lists import BanList
from nse_backtest.desk.service import check_trade

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 29, 10, 32, tzinfo=IST)


def falling_frame(n_days=40, seed=7, end="2026-10-29", last_bars=5):
    """15-minute bars of a slowly falling stock; the last session has a wide opening bar then a breakdown."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end=end, periods=n_days)
    rows, idx, px = [], [], 1000.0
    for k, d in enumerate(days):
        for b in range(last_bars if k == n_days - 1 else 25):
            o = px
            c = o * (1 + rng.normal(-0.0008, 0.003))
            h, lo = max(o, c) * (1 + rng.uniform(0, .002)), min(o, c) * (1 - rng.uniform(0, .002))
            idx.append(d + pd.Timedelta(hours=9, minutes=15 + 15 * b))
            rows.append((o, h, lo, c, 3e5 if b == 0 else 1e5))
            px = c
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex(idx))
    today = df.index[df.index.normalize() == df.index[-1].normalize()]
    o = float(df.loc[today[0], "Open"])
    df.loc[today[0], ["High", "Low", "Close"]] = [o * 1.006, o * 0.992, o * 0.993]
    for i, t in enumerate(today[1:]):
        base = o * (0.992 - 0.0005 * i)
        df.loc[t, ["Open", "High", "Low", "Close"]] = [base, base * 1.001, base * 0.9995, base * 0.9998]
    return df


LIM = Limits(capital=500_000)  # fictional


def short_ctx(**kw):
    base = dict(regime="HOSTILE", governor_can_trade=True, ban=BanList(frozenset(), "ok", "2026-10-29", ""),
                earnings_checked=True, turnover_20d=1e11, today_gap_pct=0.1, fno=frozenset({"FICT"}),
                short_edge=ShortEdge.from_trades(250, 0.12))
    base.update(kw)
    return CheckContext(NSE, LIM, NOW, **base)


def _short(entry=None, price=None):
    df = falling_frame()
    sess = df[df.index.normalize() == df.index[-1].normalize()]
    orl = float(sess.iloc[0]["Low"])
    q = LiveQuote(price=(entry if entry is not None else orl) - 0.02, day_open=float(sess.iloc[0]["Open"]),
                  as_of=NOW.replace(minute=31))
    v = check_trade(df, "FICT", NSE, Mode.INTRADAY, LIM, short_ctx(), quote=q, entry=entry, side=Side.SHORT,
                    audit=False, replays=500, used_at=NOW)
    return v, orl


# ---------------------------------------------------------------- I2: a user entry is off the pooled rule

def test_i2_short_at_a_user_entry_is_capped_at_wait():
    _, orl = _short()
    v, _ = _short(entry=round(orl - 2.0, 2))  # probe_user_entry2: two rupees below the OR low
    edge = next(c for c in v.checks if c.name == "short_edge")
    assert edge.status is Status.WARN
    assert edge.reason.endswith("; entry differs from the opening-range low the pooled history tested")
    assert v.action is Action.WAIT and "entry differs from the opening-range low" in v.headline


def test_i2_short_at_the_opening_range_low_stays_proven():
    v, orl = _short()
    assert v.plan.entry == orl
    edge = next(c for c in v.checks if c.name == "short_edge")
    assert edge.status is Status.PASS and v.action is Action.TAKE
    # within half a tick (0.05 on NSE) the entry is the OR low
    v2, _ = _short(entry=orl + 0.02)
    assert next(c for c in v2.checks if c.name == "short_edge").status is Status.PASS


def test_i2_unknown_opening_range_low_is_never_proven():
    from nse_backtest.desk.checks import short_edge_check
    from tests.desk.test_checks import sctx, splan
    c = short_edge_check(sctx(short_edge=ShortEdge.from_trades(250, 0.2), or_low=None), splan())
    assert c.status is Status.WARN and c.reason.endswith("; opening-range low unknown: entry basis not verified")
    c = short_edge_check(sctx(short_edge=ShortEdge.from_trades(250, 0.2), or_low=99.0), splan())
    assert c.status is Status.WARN and c.reason.endswith(
        "; entry differs from the opening-range low the pooled history tested")
    c = short_edge_check(sctx(short_edge=ShortEdge.from_trades(250, 0.2), or_low=100.0), splan())
    assert c.status is Status.PASS


def test_i2_entry_basis_leaves_long_hashes_alone():
    # the OR low lives on the CheckContext (never hashed), not on the TradePlan
    import dataclasses
    from nse_backtest.desk.models import TradePlan
    assert [f.name for f in dataclasses.fields(TradePlan)][-1] == "side"
    assert "or_low" in {f.name for f in dataclasses.fields(CheckContext)}
