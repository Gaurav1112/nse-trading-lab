"""H4: the Intraday tab scans the whole universe, and every scan row (TAKE/WAIT/SKIP) opens a trade card."""
from dataclasses import replace

import pandas as pd
from streamlit.testing.v1 import AppTest

from nse_backtest.desk.markets import NSE, universe
from nse_backtest.desk.models import Action, Check, Status
from tests.test_desk_card_f1 import (  # noqa: F401 - autouse fixtures are picked up from module globals
    T, _REPO, _at, _base, _boxes, _clock, _intraday, _no_live_quote, _no_network, _texts)


def _tab(monkeypatch, tmp_path, verdicts, *, fresh=None):
    from nse_backtest.desk import loaders, session
    seen = {"syms": None, "progress": None, "scans": 0, "checks": [], "override": []}

    def scan(profile, limits, symbols, regime=None, on_warning=None, on_progress=None):
        seen["scans"] += 1
        seen["syms"], seen["progress"] = list(symbols), on_progress
        if on_progress:
            on_progress(1, len(seen["syms"]), seen["syms"][0])
        return list(verdicts), []
    monkeypatch.setattr(session, "run_intraday", scan)
    monkeypatch.setattr(session, "run_check",
                        lambda p, sym, mode, limits, **k: seen["checks"].append(sym) or fresh)
    from nse_backtest.desk import audit
    monkeypatch.setattr(audit, "append_override", lambda v, path=None, **k: seen["override"].append(v))
    monkeypatch.setattr(loaders, "load_intraday", lambda p, s: pd.DataFrame(
        columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex([])))
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    at.button(key="desk_scan").click().run()
    assert not at.exception
    return at, seen


def _skip(monkeypatch, tmp_path, checks, minutes_old=1):
    _clock(monkeypatch, T)
    base = _intraday(_base(monkeypatch, tmp_path))
    return replace(_at(base, T, minutes_old), action=Action.SKIP, headline="SKIP ABC — blocked", ticket=(),
                   checks=tuple(checks))


def test_h4_scan_covers_the_whole_universe_with_progress(monkeypatch, tmp_path):
    v = _skip(monkeypatch, tmp_path, [Check("monte_carlo", Status.BLOCK, "Simulated expectancy -0.20R")])
    at, seen = _tab(monkeypatch, tmp_path, [v])
    assert seen["syms"] == list(universe(NSE)) and len(seen["syms"]) > 15
    assert callable(seen["progress"])
    at.run()
    at.run()
    assert seen["scans"] == 1  # reruns reuse the cached scan


def test_h4_skip_row_opens_a_card_with_reasons_and_the_override_box(monkeypatch, tmp_path):
    v = _skip(monkeypatch, tmp_path, [Check("monte_carlo", Status.BLOCK, "Simulated expectancy -0.20R")])
    at, seen = _tab(monkeypatch, tmp_path, [v])
    assert not [c for c in at.tabs[4].checkbox]  # nothing to override until the card is opened
    next(b for b in at.tabs[4].button if b.label == "Open card").click().run()
    assert not at.exception
    assert "Simulated expectancy -0.20R" in _texts(at.tabs[4].warning)
    assert "I choose this anyway" in [c.label for c in at.tabs[4].checkbox]
    next(c for c in at.tabs[4].checkbox if c.label == "I choose this anyway").check().run()
    at.run()
    assert len(seen["override"]) == 1  # logged once per input hash


def test_h4_data_broken_skip_opens_a_card_with_recheck_and_no_override_box(monkeypatch, tmp_path):
    v = _skip(monkeypatch, tmp_path, [Check("freshness", Status.BLOCK, "Data is stale: misses 2 sessions")])
    at, seen = _tab(monkeypatch, tmp_path, [v])
    next(b for b in at.tabs[4].button if b.label == "Open card").click().run()
    assert not at.exception
    assert any(b.label == "Re-check" for b in at.tabs[4].button)
    assert not any(c.label.startswith("I choose this anyway") for c in at.tabs[4].checkbox)
    assert seen["override"] == []


def test_h4_stale_skip_card_still_offers_recheck_only(monkeypatch, tmp_path):
    v = _skip(monkeypatch, tmp_path, [Check("monte_carlo", Status.BLOCK, "neg")], minutes_old=20)
    at, _ = _tab(monkeypatch, tmp_path, [v])
    next(b for b in at.tabs[4].button if b.label == "Open card").click().run()
    assert any(b.label == "Re-check" for b in at.tabs[4].button)
    assert "I choose this anyway" not in [c.label for c in at.tabs[4].checkbox]


# ---- H5a: a short from the scan shows its SHORT badge and a sell ticket only on override ----
def _short(monkeypatch, tmp_path, minutes_old=1):
    from nse_backtest.desk import fills
    from nse_backtest.desk.checks import SHORT_EDGE_UNPROVEN
    from nse_backtest.desk.models import Side
    _clock(monkeypatch, T)
    base = _at(_intraday(_base(monkeypatch, tmp_path)), T, minutes_old)
    p = base.plan
    r = p.entry - p.stop
    plan = replace(p, side=Side.SHORT, entry=p.stop, stop=p.entry, target_1=p.stop - 1.5 * r,
                   target_2=p.stop - 3 * r, entry_limit=fills.entry_limit_short(p.stop, p.entry, NSE.tick),
                   stop_basis="opening-range mid")
    return replace(base, plan=plan, action=Action.WAIT, odds=None, ticket=(), trigger=None,
                   current_price=plan.entry + 0.5 * r,
                   headline=f"WAIT ABC SHORT — {SHORT_EDGE_UNPROVEN}",
                   checks=(Check("short_edge", Status.WARN, SHORT_EDGE_UNPROVEN),),
                   input_hash=("s" * 40 + f"{minutes_old:04d}").ljust(64, "0"))


def test_h5a_short_row_card_badge_and_override_sell_ticket(monkeypatch, tmp_path):
    v = _short(monkeypatch, tmp_path)
    at, seen = _tab(monkeypatch, tmp_path, [v])
    df = at.tabs[4].dataframe[0].value
    assert list(df["Side"]) == ["SHORT"]
    assert any(e.label == "WAIT — ABC SHORT" for e in at.tabs[4].expander)
    next(b for b in at.tabs[4].button if b.label == "Open card").click().run()
    assert not at.exception
    assert any("SHORT</span>" in m.value for m in at.tabs[4].markdown)
    assert "edge not proven" in _texts(at.tabs[4].warning)
    next(c for c in at.tabs[4].checkbox if c.label == "I choose this anyway").check().run()
    assert not at.exception
    code = "\n".join(c.value for c in at.tabs[4].code)
    assert f"SELL {v.sizing.qty} ABC MIS SL trigger {v.plan.entry:.2f}" in code
    assert "Square off yourself by 15:10 IST (broker auto square-off charges ₹59+)" in code
    assert len(seen["override"]) == 1 and seen["override"][0].plan.is_short


def test_h5a_stale_short_recheck_keeps_the_side(monkeypatch, tmp_path):
    from nse_backtest.desk import session
    from nse_backtest.desk.models import Side
    v = _short(monkeypatch, tmp_path, minutes_old=20)
    at, _ = _tab(monkeypatch, tmp_path, [v])
    asked = []
    monkeypatch.setattr(session, "run_check", lambda p, sym, mode, limits, **k: asked.append(k) or v)
    next(b for b in at.tabs[4].button if b.label == "Open card").click().run()
    next(b for b in at.tabs[4].button if b.label == "Re-check").click().run()
    assert asked and asked[0].get("side") is Side.SHORT
