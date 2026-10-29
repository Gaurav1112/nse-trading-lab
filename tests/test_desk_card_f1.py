"""F1 final-review fixes on the trade card (components/desk_card.py) rendered in the Desk page."""
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from streamlit.testing.v1 import AppTest

from tests.test_desk_page_smoke import (  # noqa: F401 - autouse fixtures are picked up from module globals
    _REPO, _cands, _no_live_quote, _no_network, _patch_ok, _texts)

IST = ZoneInfo("Asia/Kolkata")


def _now_ist() -> datetime:
    return datetime.now(IST)


def _base(monkeypatch, tmp_path):
    """The offline TAKE (a real check over fake bars); patches the loaders first, so no network."""
    _patch_ok(monkeypatch, tmp_path)
    return _cands()[0]


def _intraday(v, *, minutes_old=0, current=None, action=None):
    """The offline TAKE turned intraday with a stop-limit, built ``minutes_old`` minutes ago."""
    from nse_backtest.desk import fills
    from nse_backtest.desk.markets import NSE
    p = v.plan
    lim = fills.entry_limit(p.entry, p.stop, NSE.tick)
    plan = replace(p, mode=__import__("nse_backtest.desk.models", fromlist=["Mode"]).Mode.INTRADAY,
                   entry_limit=lim)
    created = (_now_ist() - timedelta(minutes=minutes_old)).isoformat()
    out = replace(v, plan=plan, created_at=created, current_price=p.entry if current is None else current,
                  input_hash=("q" * 52 + f"{minutes_old:012d}"))
    return replace(out, action=action) if action is not None else out


def _card_page(monkeypatch, tmp_path, v, *, reruns=None):
    """Check a Trade with session.run_check returning ``v``; intraday bars are an empty frame (no network)."""
    from nse_backtest.desk import loaders, session
    _patch_ok(monkeypatch, tmp_path)
    monkeypatch.setattr(loaders, "load_intraday", lambda p, s: pd.DataFrame(
        columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex([])))
    calls = {"check": [], "override": []}
    seq = list(reruns or [])

    def run_check(p, sym, mode, limits, **k):
        calls["check"].append((sym, mode.value))
        return seq.pop(0) if seq and len(calls["check"]) > 1 else v
    monkeypatch.setattr(session, "run_check", run_check)
    from nse_backtest.desk import audit
    monkeypatch.setattr(audit, "append_override", lambda v, path=None, **k: calls["override"].append(v))
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    assert not at.exception
    return at, calls


def _codes(at) -> str:
    """Code blocks on the Check a Trade tab (where _card_page renders the card)."""
    return " | ".join(c.value for c in at.tabs[2].code)


def _all_text(at) -> str:
    return " | ".join([_texts(at.caption), _texts(at.markdown), _texts(at.info), _texts(at.warning),
                       _texts(at.error)])


# ---------------------------------------------------------------- A: money honesty

def test_a1_intraday_card_shows_worst_fill_max_loss_trigger_line_and_outcome_caveat(monkeypatch, tmp_path):
    from nse_backtest.desk.markets import NSE
    from nse_backtest.desk.sizing import loss_at_stop
    v = _intraday(_base(monkeypatch, tmp_path))
    at, _ = _card_page(monkeypatch, tmp_path, v)
    labels = {m.label: m.value for m in at.metric}
    n = v.sizing.qty
    assert labels["Max loss"] == NSE.money(loss_at_stop(v.plan, NSE, n))
    text = _all_text(at)
    assert f"Max loss if filled at the trigger: {NSE.money(loss_at_stop(v.plan, NSE, n, entry=v.plan.entry))}" in text
    assert "(the order's limit), which is already in the max loss" in text
    slip = NSE.money(n * (v.plan.entry_limit - v.plan.entry))
    assert f"assumes a fill at the trigger; a fill at the limit worsens each outcome by up to {slip}" in text


def test_a3_daily_loss_breach_says_it_is_this_trade_alone(monkeypatch, tmp_path):
    v = _base(monkeypatch, tmp_path)
    at, _ = _card_page(monkeypatch, tmp_path, v)
    qty = next(n for n in at.number_input if n.label == "Quantity")
    qty.set_value(int(qty.value) * 50).run()
    assert "exceeds the daily loss limit" in _texts(at.error)
    assert ("compares this trade alone with the daily loss limit; losses already taken today are checked by the "
            "daily-loss check in Checks") in _texts(at.caption)


# ---------------------------------------------------------------- B: override and staleness honesty

T = datetime(2026, 10, 29, 11, 0, tzinfo=IST)  # the card's "now" in these tests


def _at(v, now: datetime, minutes_old: int):
    return replace(v, created_at=(now - timedelta(minutes=minutes_old)).isoformat(),
                   input_hash=("b" * 40 + f"{minutes_old:04d}{now:%H%M}").ljust(64, "0"))


def _clock(monkeypatch, now: datetime):
    from components import desk_card
    monkeypatch.setattr(desk_card, "market_now", lambda profile: now)


def _boxes(at):
    return [c.label for c in at.checkbox]


def test_b1_old_intraday_skip_has_no_override_box_and_recheck_replaces_the_verdict(monkeypatch, tmp_path):
    from nse_backtest.desk.models import Action, Check, Status
    _clock(monkeypatch, T)
    base = _intraday(_base(monkeypatch, tmp_path))
    old = replace(_at(base, T, 16), action=Action.SKIP, headline="SKIP ABC — blocked by: window",
                  checks=(Check("window", Status.BLOCK, "outside"),))
    fresh = _at(base, T, 0)  # a TAKE built now
    at, calls = _card_page(monkeypatch, tmp_path, old, reruns=[fresh])
    assert "I choose this anyway" not in _boxes(at)
    assert "This intraday verdict is 16 min old — re-check before choosing it" in _texts(at.warning)
    assert "built 10:44 (16 min ago)" in _texts(at.caption)
    at.button(key=next(b.key for b in at.button if b.label == "Re-check")).click().run()
    assert not at.exception
    assert calls["check"][-1] == ("ABC", "intraday") and len(calls["check"]) == 2
    assert "16 min old" not in _texts(at.warning)
    assert "built 11:00 (0 min ago)" in _texts(at.caption)
    assert "BUY" in _codes(at) and calls["override"] == []


def test_b2_old_intraday_take_hides_the_ticket(monkeypatch, tmp_path):
    _clock(monkeypatch, T)
    v = _at(_intraday(_base(monkeypatch, tmp_path)), T, 20)
    at, _ = _card_page(monkeypatch, tmp_path, v)
    assert "BUY" not in _codes(at)
    assert "This intraday verdict is 20 min old — re-check before choosing it" in _texts(at.warning)
    assert any(b.label == "Re-check" for b in at.button)


def test_b2_fresh_intraday_take_keeps_its_ticket_and_shows_its_age(monkeypatch, tmp_path):
    _clock(monkeypatch, T)
    v = _at(_intraday(_base(monkeypatch, tmp_path)), T, 2)
    at, _ = _card_page(monkeypatch, tmp_path, v)
    assert "BUY" in _codes(at) and "built 10:58 (2 min ago)" in _texts(at.caption)
    assert not any(b.label == "Re-check" for b in at.button)


def test_b2_intraday_take_past_the_entry_cutoff_hides_the_ticket(monkeypatch, tmp_path):
    late = datetime(2026, 10, 29, 14, 40, tzinfo=IST)
    _clock(monkeypatch, late)
    v = _at(_intraday(_base(monkeypatch, tmp_path)), late, 5)
    at, _ = _card_page(monkeypatch, tmp_path, v)
    assert "BUY" not in _codes(at)
    assert "past the entry cutoff (14:30)" in _texts(at.warning)
    assert any(b.label == "Re-check" for b in at.button)


def test_b2_old_swing_take_keeps_its_ticket_with_the_age(monkeypatch, tmp_path):
    _clock(monkeypatch, T)
    v = _at(_base(monkeypatch, tmp_path), T, 120)
    at, _ = _card_page(monkeypatch, tmp_path, v)
    assert "BUY" in _codes(at) and "built 09:00 (120 min ago)" in _texts(at.caption)


def test_b2_choose_table_has_a_built_column(monkeypatch, tmp_path):
    from tests.test_desk_page_smoke import _choose_page, _load
    at, _ = _choose_page(monkeypatch, tmp_path)
    _load(at)
    df = next(d.value for d in at.dataframe if "Desk says" in d.value.columns)
    assert "Built" in df.columns
    import re
    assert all(re.fullmatch(r"\d\d:\d\d", x) for x in df["Built"].iloc[:4])
    assert df[df["Symbol"] == "ZZZ"].iloc[0]["Built"] == "—"


# ---------------------------------------------------------------- D2: safe error text on the card and page

def _raw(els) -> str:
    return " | ".join(e.value for e in els)


def test_d2_card_errors_never_show_the_home_dir_or_markdown(monkeypatch, tmp_path):
    from pathlib import Path
    from nse_backtest.desk import audit, loaders
    from nse_backtest.desk.models import Action, Check, Status
    home = str(Path.home())
    v = replace(_base(monkeypatch, tmp_path), action=Action.SKIP, checks=(Check("gap", Status.BLOCK, "wide"),))
    at, _ = _card_page(monkeypatch, tmp_path, v)

    def bad_override(*a, **k):
        raise PermissionError(13, "Permission denied", f"{home}/.nse-trading-lab/desk_audit.jsonl")
    monkeypatch.setattr(audit, "append_override", bad_override)
    monkeypatch.setattr(loaders, "corporate_actions", lambda p, s: (_ for _ in ()).throw(
        OSError(f"cache at file://{home}/x/cache.db is [locked](https://evil.test) for $5B")))
    next(c for c in at.checkbox if c.label == "I choose this anyway").check().run()
    next(b for b in at.button if b.label == "Load news & earnings").click().run()
    raw, read = _raw(at.error) + _raw(at.warning), _texts(at.error) + _texts(at.warning)
    assert "Override NOT logged" in read and "desk_audit.jsonl" in read
    assert "Research unavailable" in read and "cache.db" in read
    assert home not in read and home not in raw
    assert "](https://evil.test)" not in raw and r"\$5B" in raw  # shown as text, never as a link or maths


def test_d2_check_failed_text_is_scrubbed(monkeypatch, tmp_path):
    from pathlib import Path
    from nse_backtest.desk import session
    home = str(Path.home())
    _patch_ok(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise RuntimeError(f"bars cache {home}/data/abc.parquet unreadable")
    monkeypatch.setattr(session, "run_check", boom)
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    err = _texts(at.error)
    assert "Check failed" in err and "abc.parquet" in err and home not in err and home not in _raw(at.error)



# ---------------------------------------------------------------- E2: research keeps the earnings date

def test_e2_corporate_actions_failure_still_shows_the_earnings_date(monkeypatch, tmp_path):
    from datetime import date
    from nse_backtest.desk import loaders
    v = _base(monkeypatch, tmp_path)
    at, _ = _card_page(monkeypatch, tmp_path, v)
    monkeypatch.setattr(loaders, "next_earnings", lambda p, s: (date(2026, 11, 12), True))
    monkeypatch.setattr(loaders, "corporate_actions", lambda p, s: (_ for _ in ()).throw(ValueError("HTTP 500")))
    next(b for b in at.button if b.label == "Load news & earnings").click().run()
    assert not at.exception
    text = _texts(at.markdown) + _texts(at.caption)
    assert "Next earnings" in text and "2026-11-12" in text
    assert "dividends/splits unavailable: HTTP 500" in text
    assert "Research unavailable" not in _texts(at.warning)
