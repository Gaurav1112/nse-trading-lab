import json
import numpy as np, pandas as pd, pytest
from nse_backtest.desk import cli, loaders
from nse_backtest.desk.markets import NSE, US
from nse_backtest.desk.models import Limits


def series(n=400):
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=n)
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
