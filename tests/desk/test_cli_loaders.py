import json
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd, pytest
from nse_backtest.desk import calendars as cal, cli, loaders
from nse_backtest.desk.markets import NSE, US
from nse_backtest.desk.models import Limits


def _last_completed_session(prof=NSE):
    """The newest NSE session that has closed as of the wall clock, so the swing freshness check
    passes at any time of day (a fixed "yesterday" goes stale once today's session closes)."""
    now = datetime.now(ZoneInfo(prof.tz))
    d = now.date()
    if cal.is_trading_day(prof, d) and now.time() >= cal.session_close_for(prof, d):
        return d
    return cal.previous_trading_day(prof, d)


def series(n=400):
    idx = pd.bdate_range(end=pd.Timestamp(_last_completed_session()), periods=n)
    close = 100 * 1.004 ** np.arange(1, n + 1); prev = close / 1.004
    return pd.DataFrame({"Open": prev, "High": prev * 1.012, "Low": prev * 0.995, "Close": close, "Volume": 5e6}, index=idx)


@pytest.fixture(autouse=True)
def isolated(offline_desk):
    pass


def test_limits_roundtrip_per_market(tmp_path):
    loaders.save_limits(NSE, Limits(capital=123_456))
    assert loaders.load_limits(NSE).capital == 123_456 and loaders.load_limits(US).capital == 10_000


def test_cli_check_text(capsys):
    assert cli.main(["check", "ABC", "--market", "NSE"]) == 0
    out = capsys.readouterr().out
    assert any(out.startswith(w) for w in ("TAKE", "WAIT", "SKIP")) and "Nothing was placed" in out
    assert "PASS" in out or "WARN" in out


def test_cli_check_json(capsys):
    assert cli.main(["check", "ABC", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["plan"]["symbol"] == "ABC" and d["action"] in ("TAKE", "WAIT", "SKIP")


def test_cli_bad_stop_exits_2(capsys):
    assert cli.main(["check", "ABC", "--entry", "100", "--stop", "105"]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_settings(capsys):
    assert cli.main(["settings", "--market", "US", "--capital", "2500", "--pdt-guard", "on"]) == 0
    l = loaders.load_limits(US)
    assert l.capital == 2500 and l.pdt_guard is True


def test_cli_check_corrupt_journal_never_passes_daily_loss(tmp_path, capsys):
    (tmp_path / "trade_journal.json").write_text('{"a": 1}')
    assert cli.main(["check", "ABC"]) == 0
    out = capsys.readouterr().out
    assert "PASS daily_loss" not in out and "WARN daily_loss" in out


def test_c1_noise_label_in_cli():
    from dataclasses import replace
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from nse_backtest.desk.verdict import decide
    from tests.desk.test_checks import ODDS, SIZE, plan
    v = decide(plan(), SIZE, replace(ODDS, noise_expectancy_min=-0.1, noise_expectancy_max=0.3), (), NSE, 100.0,
               datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo(NSE.tz)))
    text = "\n".join(cli.format_verdict(v, NSE))
    assert "Candle-noise expectancy range: -0.10R to +0.30R" in text and "shuffled" not in text



def test_d2_no_unused_imports_left():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    flagged = {"nse_backtest/desk/intraday.py": "replace", "nse_backtest/desk/cli.py": "Limits",
               "nse_backtest/desk/verdict.py": "Check", "pages/0_Desk.py": "Limits"}
    for rel, name in flagged.items():
        tree = ast.parse((root / rel).read_text())
        imported = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert name not in imported, f"{rel} still imports {name}"
