"""pipeline.run must surface degraded data sources in the health file, never silently."""
import json
from datetime import datetime, timezone

import pytest

import pipeline.fetch
import pipeline.push
import pipeline.run as run
from pipeline.compute import Signal, SignalBatch
from pipeline.fetch import LTPQuote


def _batch(signals=()):
    return SignalBatch(generated_at=datetime.now(timezone.utc), regime="NEUTRAL",
                       regime_conditions={}, swing_signals=list(signals))


def _signal():
    return Signal(signal_id="sig-1", mode="SWING", action="BUY", symbol="AAA",
                  entry=100.0, stop_loss=98.0, target=103.0, tape_regime="NEUTRAL", thesis="t")


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Run main() offline: no network, no git; health is captured."""
    health = {}
    monkeypatch.setenv("SIGNALS_REPO_PATH", str(tmp_path))
    monkeypatch.setattr(run, "NIFTY50_SYMBOLS", ["AAA"])
    monkeypatch.setattr(run, "is_market_hours", lambda now: True)
    monkeypatch.setattr(run, "fetch_nifty50", lambda start: None)
    monkeypatch.setattr(run, "persist_batch", lambda *a, **k: None)
    monkeypatch.setattr(run, "persist_health_only", lambda p: None)
    monkeypatch.setattr(run, "write_health",
                        lambda path, status, errors, ts: health.update(status=status, errors=list(errors)))
    monkeypatch.setattr(run, "compute_signal_batch", lambda fresh, df, quote_source: _batch())
    yq = {"AAA": LTPQuote("AAA", 100.0, datetime.now(timezone.utc), "yfinance")}
    monkeypatch.setattr(pipeline.fetch, "fetch_yfinance_ltp", lambda syms: yq)
    return health, tmp_path


def test_missing_fyers_package_degrades_health(harness, monkeypatch):
    health, _ = harness
    def no_pkg():
        raise pipeline.fetch.FyersUnavailableError("fyers_apiv3 is not installed")
    monkeypatch.setattr(pipeline.fetch, "_fyers_client", no_pkg)
    assert run.main() == 0  # same exit code as every other non-crash degraded run
    assert health["status"] == "degraded"
    assert "quotes fell back to yfinance: fyers_apiv3 is not installed" in health["errors"]


def test_missing_fyers_credentials_degrades_health(harness, monkeypatch):
    import sys, types
    health, _ = harness
    fake = types.ModuleType("fyers_apiv3")  # package present, so only the credentials are missing
    fake.fyersModel = types.SimpleNamespace(FyersModel=lambda **k: pytest.fail("must not build a client"))
    monkeypatch.setitem(sys.modules, "fyers_apiv3", fake)
    monkeypatch.delenv("FYERS_APP_ID", raising=False)
    monkeypatch.delenv("FYERS_ACCESS_TOKEN", raising=False)
    assert run.main() == 0
    assert health["status"] == "degraded"
    assert "quotes fell back to yfinance: FYERS_APP_ID / FYERS_ACCESS_TOKEN are not set" in health["errors"]


def test_fyers_quotes_stay_healthy(harness, monkeypatch):
    health, _ = harness
    fq = {"AAA": LTPQuote("AAA", 100.0, datetime.now(timezone.utc), "fyers")}
    monkeypatch.setattr(pipeline.fetch, "fetch_fyers_ltp", lambda syms: fq)
    assert run.main() == 0
    assert health == {"status": "healthy", "errors": []}


def test_push_disabled_is_reported_and_subscriptions_kept(harness, monkeypatch):
    health, root = harness
    fq = {"AAA": LTPQuote("AAA", 100.0, datetime.now(timezone.utc), "fyers")}
    monkeypatch.setattr(pipeline.fetch, "fetch_fyers_ltp", lambda syms: fq)
    monkeypatch.setattr(run, "compute_signal_batch", lambda f, d, quote_source: _batch([_signal(), _signal()]))
    monkeypatch.setattr(pipeline.push, "webpush", None)
    monkeypatch.setenv("VAPID_PRIVATE_KEY_PEM", "fictional-key")
    subs_path = root / "state" / "push_subscriptions.json"
    subs_path.parent.mkdir(parents=True)
    subs = [{"endpoint": "https://push.example.invalid/a", "keys": {"p256dh": "x", "auth": "y"}}]
    subs_path.write_text(json.dumps(subs))
    assert run.main() == 0
    assert health["status"] == "degraded"
    assert health["errors"] == ["push disabled: pywebpush not installed"]  # once, not per signal
    assert not any(e.startswith("push_failed") for e in health["errors"])
    assert json.loads(subs_path.read_text()) == subs
