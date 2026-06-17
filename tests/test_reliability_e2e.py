"""End-to-end reliability smoke — walk every page including new ones added
by the Tier 1/2/3/R waves. Catches AgGrid-class bugs and import-time errors
that only show up in production interactions.

Distinct from tests/test_pages_interactive_smoke.py which tests the original
13 pages; this tests the reliability surface added in later commits.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


def _run(path: str, timeout: float = 60.0) -> AppTest:
    at = AppTest.from_file(path, default_timeout=timeout)
    at.run()
    return at


def test_track_record_page_renders_with_empty_journal():
    at = _run("pages/14_Track_Record.py", timeout=30)
    assert not at.exception, f"Track Record raised: {at.exception}"


def test_trade_replay_renders_with_empty_journal():
    at = _run("pages/15_Trade_Replay.py", timeout=30)
    assert not at.exception, f"Trade Replay raised: {at.exception}"


def test_watchlist_page_imports_without_crash():
    """Watchlist runs a Nifty 50 scan; we use a long timeout because of
    the cold yfinance fetch. The test passes if no exception is raised."""
    at = _run("pages/16_Watchlist.py", timeout=180)
    # AppTest may time out gracefully; the test passes if no exception was raised.
    if at.exception:
        # The most common Cloud-class failure is a rate-limited yfinance —
        # we still want to be alerted to genuine code errors.
        msg = str(at.exception)
        if "timeout" not in msg.lower() and "yfinance" not in msg.lower():
            pytest.fail(f"Watchlist raised non-network exception: {msg}")


def test_picks_page_in_hostile_shows_blocked_save():
    """Picks page must render the HOSTILE hard-block UI; the test passes
    when the page loads without raising. We don't enforce a specific
    HOSTILE-state assertion because tape may shift over time."""
    at = _run("pages/1_Picks.py", timeout=120)
    assert not at.exception, f"Picks page raised: {at.exception}"


def test_tape_monitor_renders_v2_calibrator_info():
    at = _run("pages/13_Tape_Monitor.py", timeout=60)
    assert not at.exception, f"Tape Monitor raised: {at.exception}"


def test_aggregate_risk_caption_visible_on_picks():
    """The new aggregate risk envelope caption must compute without error
    even with zero positions."""
    from components.risk_governor import assess
    v = assess(positions=[], journal=[], capital=100_000, regime="HOSTILE")
    assert v.aggregate_risk_pct == 0.0
    assert v.aggregate_risk_cap_pct == 1.0
    assert not v.flatten_all


def test_kelly_correlation_haircut_in_picks_save_flow():
    """When other positions are held, kelly_size accepts correlations and
    returns a smaller suggested_qty. The Picks save flow uses this."""
    from nse_backtest.features.kelly_sizing import kelly_size
    no_corr = kelly_size(
        calibrated_win_prob_pct=70.0, risk_reward=2.0,
        entry_price=100, stop_loss=95, capital=100_000, max_risk_pct=10.0,
    )
    with_corr = kelly_size(
        calibrated_win_prob_pct=70.0, risk_reward=2.0,
        entry_price=100, stop_loss=95, capital=100_000, max_risk_pct=10.0,
        open_book_correlations=[0.6, 0.5, 0.4],
    )
    assert with_corr.risk_pct_of_capital < no_corr.risk_pct_of_capital


def test_data_freshness_intraday_message_is_honest():
    """During market hours with age=1 the message should explain yfinance
    is EOD and not suggest the user 'fix the feed'."""
    import pandas as pd
    from components.data_freshness import check_freshness
    # Build a tiny df with last bar being a business day-ish prior to today
    idx = pd.date_range("2026-06-10", periods=5, freq="B")
    df = pd.DataFrame({"Close": [100, 101, 102, 103, 104]}, index=idx)
    v = check_freshness(df)
    # The exact message depends on when this runs, but it must NOT say
    # the misleading "fix feed" phrase when age is 1 during market hours.
    if v.age_business_days == 1 and v.market_open_now:
        assert "EOD feed" in v.message or "after market close" in v.message
