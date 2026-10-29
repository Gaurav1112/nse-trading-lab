from __future__ import annotations
import json
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from pipeline.compute import SignalBatch


def _signal_to_dict(s) -> dict:
    """Convert Signal to dict, serializing datetime to ISO format."""
    d = asdict(s)
    d["generated_at"] = s.generated_at.isoformat()
    return d


def _batch_to_dict(batch: SignalBatch) -> dict:
    """Convert SignalBatch to dict, serializing generated_at to ISO format."""
    return {
        "generated_at": batch.generated_at.isoformat(),
        "regime": batch.regime,
        "regime_conditions": batch.regime_conditions,
        "signals": [_signal_to_dict(s) for s in batch.swing_signals],
        "quote_source": batch.regime_conditions.get("quote_source", "unknown"),
    }


def persist_batch(batch: SignalBatch, repo_path: Path, push: bool = True) -> None:
    """Write latest.json + append daily audit log + (optionally) git-push.

    Args:
        batch: SignalBatch to persist
        repo_path: Path to signals repo (contains state/ and signals/ dirs)
        push: If True, git add/commit/push; if False, only write files
    """
    repo_path = Path(repo_path)
    state_dir = repo_path / "state"
    state_dir.mkdir(exist_ok=True)

    payload = _batch_to_dict(batch)
    (state_dir / "latest.json").write_text(json.dumps(payload, indent=2))

    day = batch.generated_at.strftime("%Y-%m-%d")
    hhmm = batch.generated_at.strftime("%H%M")
    day_dir = repo_path / "signals" / day
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / f"{hhmm}.json").write_text(json.dumps(payload, indent=2))

    if push:
        _git_commit_and_push(repo_path, f"chore(pipeline): batch {day} {hhmm}")


def write_health(path: Path, status: str, errors: list[str], last_run_ts: datetime) -> None:
    """Write pipeline health status to file.

    Args:
        path: Path to health JSON file
        status: Status string (e.g., "healthy", "error")
        errors: List of error messages
        last_run_ts: Timestamp of last run
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "status": status,
        "errors": errors,
        "last_run_ts": last_run_ts.isoformat(),
    }, indent=2))


def _git_commit_and_push(repo_path: Path, msg: str) -> None:
    """Git add/commit/push with pipeline identity.

    Only run in production when push=True. Tests use push=False to avoid
    any git operations.
    """
    subprocess.check_call(["git", "-C", str(repo_path), "add", "-A"])
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--cached", "--quiet"],
        check=False,
    )
    if result.returncode == 0:
        return  # nothing to commit
    subprocess.check_call([
        "git", "-C", str(repo_path), "-c", "user.email=pipeline@nse-trading-lab",
        "-c", "user.name=nse-pipeline", "commit", "-m", msg,
    ])
    subprocess.check_call(["git", "-C", str(repo_path), "push", "origin", "HEAD:main"])
