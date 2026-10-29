"""Research panel adapters: subprocess isolation, field mapping, display-only guarantee. No network."""
import ast
import subprocess
import sys
from pathlib import Path

import pandas as pd

from nse_backtest.desk import research
from nse_backtest.desk.research import (Research, ResearchItem, Unavailable, nse_research,
                                        run_skill, us_research)

FAKE = Path(__file__).parent / "fixtures" / "fake_skill.py"
DESK = Path(research.__file__).parent


def test_run_skill_parses_dict():
    out = run_skill(FAKE, ["ok", "x"])
    assert out == {"args": ["x"]}


def test_run_skill_wraps_list():
    assert run_skill(FAKE, ["list"]) == {"results": [{"a": 1}]}


def test_run_skill_timeout():
    out = run_skill(FAKE, ["sleep"], timeout=0.5)
    assert out == Unavailable("timed out after 0.5s")
    assert research.DEFAULT_TIMEOUT == 10.0


def test_run_skill_failure_uses_last_stderr_line_without_paths():
    out = run_skill(FAKE, ["fail"])
    assert isinstance(out, Unavailable)
    assert "boom" in out.reason and "/Users" not in out.reason and "someone" not in out.reason


def test_run_skill_garbage():
    assert run_skill(FAKE, ["garbage"]) == Unavailable("bad output")


def test_run_skill_exit1_with_json_is_accepted():
    assert run_skill(FAKE, ["empty1"]) == {"items": []}


def test_run_skill_missing_script():
    out = run_skill(FAKE.with_name("nope.py"), [])
    assert isinstance(out, Unavailable) and "/" not in out.reason


def test_run_skill_command_cwd_env(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Person test@example.org")
    monkeypatch.setenv("SECRET_TOKEN", "x")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(research.subprocess, "run", fake_run)
    run_skill(FAKE, ["ok"])
    assert seen["cmd"] == [sys.executable, "-I", str(FAKE), "ok"]
    assert set(seen["kw"]["env"]) <= {"PATH", "HOME", "SEC_USER_AGENT"}
    assert "SEC_USER_AGENT" in seen["kw"]["env"] and "SECRET_TOKEN" not in seen["kw"]["env"]
    assert seen["kw"]["timeout"] == 10.0
    assert not seen["kw"].get("shell")
    assert Path(seen["kw"]["cwd"]) != Path.cwd()


def test_run_skill_real_env_and_temp_cwd(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setenv("SECRET_TOKEN", "x")
    out = run_skill(FAKE, ["env"])
    assert "SECRET_TOKEN" not in out["env"] and "SEC_USER_AGENT" not in out["env"]
    assert Path(out["cwd"]).resolve() != Path.cwd().resolve()


def test_invalid_us_symbols_never_spawn(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("subprocess started")
    monkeypatch.setattr(research.subprocess, "run", boom)
    for s in ("A B", "../x", "", "1ABC", "a" * 11, None):
        assert isinstance(us_research(s), Unavailable)


NEWS = {"checked": "2026-10-29T10:00:00+00:00", "moves": [], "notes": ["feed slow"], "items": [
    {"kind": "headline", "title": "ACME beats", "link": "https://x/1", "time": "2026-10-28T12:00:00+00:00"},
    {"kind": "filing", "form": "8-K", "title": "Acme filed a 8-K: earnings results",
     "link": "https://sec/1", "time": "2026-10-27T09:00:00+00:00"},
    {"kind": "filing", "form": "10-Q", "title": "other", "link": "", "time": None}]}
FACTS = {"results": [{"ticker": "ACME", "result": {"rows": [{"end": "2026-09-30"}]},
                      "filings": {"report": {"url": "https://sec/10q"},
                                  "earnings_8k": {"url": "https://sec/8k", "filed": "2026-10-20"}},
                      "changes": ["Quarter ending 2026-09-30: revenue $1.0B."], "note": "stale?"}]}


def runner_for(news=NEWS, facts=FACTS):
    calls = []

    def runner(script, args, **kw):
        calls.append((script.name, args))
        return news if script.name == "news.py" else facts
    runner.calls = calls
    return runner


def test_us_research_maps_fields(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Person test@example.org")
    r = runner_for()
    out = us_research("ACME", runner=r)
    assert isinstance(out, Research) and out.source == "US skills"
    kinds = [(i.kind, i.title) for i in out.items]
    assert ("headline", "ACME beats") in kinds
    assert ("8-K", "Acme filed a 8-K: earnings results") in kinds
    assert not any(t == "other" for _, t in kinds)  # only 8-K filings are shown
    e = [i for i in out.items if i.kind == "earnings"][0]
    assert e.when == "2026-09-30" and e.url == "https://sec/8k" and "revenue" in e.title
    assert "feed slow" in out.notes and "stale?" in out.notes
    names = [c[0] for c in r.calls]
    assert names == ["news.py", "facts.py"]
    nargs = r.calls[0][1]
    assert "--dry-run" in nargs and "--json" in nargs and nargs[nargs.index("--hours") + 1] == "168"


def test_us_research_without_sec_ua(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    r = runner_for()
    out = us_research("ACME", runner=r)
    assert "SEC data unavailable: set SEC_USER_AGENT" in out.notes
    assert [c[0] for c in r.calls] == ["news.py"]
    assert "--no-sec" in r.calls[0][1]
    assert any(i.kind == "headline" for i in out.items)
    assert not any(i.kind == "earnings" for i in out.items)


def test_us_research_partial_and_total_failure(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Person test@example.org")

    def facts_down(script, args, **kw):
        return NEWS if script.name == "news.py" else Unavailable("no SEC CIK")
    out = us_research("ACME", runner=facts_down)
    assert isinstance(out, Research) and any("no SEC CIK" in n for n in out.notes)

    def all_down(script, args, **kw):
        return Unavailable("timed out after 10s")
    assert us_research("ACME", runner=all_down) == Unavailable("timed out after 10s")


def test_nse_research_maps():
    idx = pd.to_datetime(["2026-05-10", "2026-06-01", "2026-07-01"])
    actions = pd.DataFrame({"Dividends": [5.0, 0.0, 0.0], "Stock Splits": [0.0, 2.0, 0.0]}, index=idx)
    from datetime import date
    out = nse_research("INFY", earnings=date(2026, 11, 12), actions=actions)
    assert out.source == "NSE"
    byk = {i.kind: i for i in out.items}
    assert byk["earnings"].when == "2026-11-12"
    assert byk["dividend"].when == "2026-05-10" and "5" in byk["dividend"].title
    assert byk["split"].when == "2026-06-01" and "2" in byk["split"].title
    assert all(i.url is None for i in out.items)


def test_nse_research_missing_and_invalid():
    assert isinstance(nse_research("INFY"), Unavailable)
    assert isinstance(nse_research("../x"), Unavailable)
    from datetime import date
    out = nse_research("INFY", earnings=date(2026, 11, 12))
    assert any("dividend" in n.lower() for n in out.notes)


def test_isolation_no_decision_module_imports_research():
    names = "checks verdict service plan sizing timemachine daily intraday scoreboard scoreboard_io " \
            "tradecard projections charts fills".split()
    for n in names:
        tree = ast.parse((DESK / f"{n}.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [(node.module or "")] + [f"{node.module}.{a.name}" for a in node.names]
                if node.level:
                    mods += [a.name for a in node.names]
            else:
                continue
            assert not any(m.split(".")[-1] == "research" for m in mods), (n, mods)


def test_research_imports_only_markets_from_desk():
    tree = ast.parse((DESK / "research.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                assert mod in ("markets", "") and all(a.name == "markets" for a in node.names) or mod == "markets"
            elif mod.startswith("nse_backtest.desk"):
                assert mod == "nse_backtest.desk.markets" or (
                    mod == "nse_backtest.desk" and all(a.name == "markets" for a in node.names))
        elif isinstance(node, ast.Import):
            assert not any(a.name.startswith("nse_backtest.desk") for a in node.names)
