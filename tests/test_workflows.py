"""F1 D5: the CI gates and the heartbeat script, run for real (bash / python) from the workflow files."""
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WF = REPO / ".github" / "workflows"
FILES = ("signal-pipeline.yml", "heartbeat.yml")


def _run_block(text: str, step: str) -> str:
    """The `run: |` script of the step named ``step`` (literal block, dedented)."""
    lines = text.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.strip() == f"- name: {step}")
    j = next(k for k in range(i + 1, len(lines)) if lines[k].strip() == "run: |")
    indent = len(lines[j]) - len(lines[j].lstrip())
    out = []
    for ln in lines[j + 1:]:
        if ln.strip() and len(ln) - len(ln.lstrip()) <= indent:
            break
        out.append(ln)
    return textwrap.dedent("\n".join(out))


@pytest.mark.parametrize("name", FILES)
def test_workflow_token_is_read_only(name):
    text = (WF / name).read_text()
    assert re.search(r"^permissions:\n  contents: read\n", text, re.M), "top-level permissions: contents: read"


def _gate(name, tmp_path, **env):
    script = _run_block((WF / name).read_text(), "Check pipeline secrets")
    out = tmp_path / "out"
    out.write_text("")
    full = {"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(out), **env}
    p = subprocess.run(["bash", "-e", "-c", script], env=full, capture_output=True, text=True)
    return p, out.read_text()


@pytest.mark.parametrize("name", FILES)
def test_gate_skips_without_secrets_and_runs_with_both(name, tmp_path):
    p, out = _gate(name, tmp_path)
    assert p.returncode == 0 and "configured=false" in out and "::notice::" in p.stdout
    p, out = _gate(name, tmp_path, SIGNALS_REPO_URL="git@github.com:x/y.git", DEPLOY_KEY="k")
    assert p.returncode == 0 and "configured=true" in out


@pytest.mark.parametrize("name", FILES)
@pytest.mark.parametrize("one", [{"SIGNALS_REPO_URL": "git@github.com:x/y.git"}, {"DEPLOY_KEY": "k"}])
def test_gate_fails_when_exactly_one_secret_is_set(name, one, tmp_path):
    p, out = _gate(name, tmp_path, **one)
    assert p.returncode == 1 and "::error::" in p.stdout and "configured=true" not in out


# ---------------------------------------------------------------- heartbeat script

def _heartbeat(tmp_path, health: dict | None, subs=None, push_fails=()):
    import json
    script = _run_block((WF / "heartbeat.yml").read_text(), "Check + alert")
    py = script.split("<<'PYEOF'\n", 1)[1].rsplit("PYEOF", 1)[0]
    root = tmp_path / "signals"
    (root / "state").mkdir(parents=True)
    if health is not None:
        (root / "state" / "pipeline_health.json").write_text(json.dumps(health))
    if subs is not None:
        (root / "state" / "push_subscriptions.json").write_text(json.dumps(subs))
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "pywebpush.py").write_text(textwrap.dedent(f"""
        FAILS = {list(push_fails)!r}
        def webpush(subscription_info, data, vapid_private_key, vapid_claims):
            if subscription_info["endpoint"] in FAILS:
                raise RuntimeError("SECRET-TOKEN-in-exception " + subscription_info["endpoint"])
    """))
    (tmp_path / "hb.py").write_text(py.replace("/tmp/signals", str(root)))
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(stub), "VAPID_PRIVATE_KEY_PEM": "k", "VAPID_CONTACT": "c"}
    return subprocess.run([sys.executable, str(tmp_path / "hb.py")], env=env, capture_output=True, text=True,
                          cwd=tmp_path)


def test_heartbeat_fails_when_the_health_file_has_no_last_run_ts(tmp_path):
    p = _heartbeat(tmp_path, {"status": "ok"})
    assert p.returncode == 1 and "health file has no last_run_ts" in p.stdout + p.stderr


def test_heartbeat_ok_when_fresh(tmp_path):
    from datetime import datetime, timezone
    p = _heartbeat(tmp_path, {"last_run_ts": datetime.now(timezone.utc).isoformat()})
    assert p.returncode == 0 and "OK" in p.stdout


def test_heartbeat_fails_when_every_push_fails_and_never_prints_the_exception(tmp_path):
    subs = [{"endpoint": "a"}, {"endpoint": "b"}]
    p = _heartbeat(tmp_path, {"last_run_ts": "2026-01-01T00:00:00+00:00"}, subs, push_fails=("a", "b"))
    out = p.stdout + p.stderr
    assert p.returncode == 1 and "2 of 2" in out and "SECRET-TOKEN" not in out


def test_heartbeat_alert_delivered_to_some_is_ok(tmp_path):
    subs = [{"endpoint": "a"}, {"endpoint": "b"}]
    p = _heartbeat(tmp_path, {"last_run_ts": "2026-01-01T00:00:00+00:00"}, subs, push_fails=("a",))
    out = p.stdout + p.stderr
    assert p.returncode == 0 and "1 of 2" in out and "SECRET-TOKEN" not in out


def test_heartbeat_dead_with_an_empty_subscriber_list_fails(tmp_path):
    p = _heartbeat(tmp_path, {"last_run_ts": "2026-01-01T00:00:00+00:00"}, [])
    assert p.returncode == 1 and "0 of 0" in p.stdout


def test_heartbeat_installs_the_requirements_ranges():
    import re as _re
    req = {m.group(1): m.group(2) for m in _re.finditer(r"^([a-zA-Z0-9_.\-]+)(>=[^\s#]+)", (REPO / "requirements.txt")
                                                          .read_text(), _re.M)}
    text = (WF / "heartbeat.yml").read_text()
    for pkg in ("pywebpush", "py-vapid", "requests"):
        assert f'"{pkg}{req[pkg]}"' in text, pkg
