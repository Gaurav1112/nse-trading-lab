# Trustable Intraday + Swing Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the NSE Trading Lab as an always-on, alert-driven, trust-first intraday + swing decision system with live pricing, PWA push notifications, an append-only trust ledger, and an evidence-based signal engine — consolidating 17 pages into 6 visible + Advanced drawer.

**Architecture:** Three cooperating pieces — (1) a GitHub Actions pipeline running every 5 min during NSE hours in the public `nse-trading-lab` repo, (2) an append-only trust ledger in a private `nse-trading-lab-signals` repo, (3) a Streamlit UI installable as a Chrome PWA that receives Web Push notifications. Everything reproducible, everything loud-when-broken.

**Tech Stack:** Python 3.11+, Streamlit, GitHub Actions, yfinance, Fyers API (fyers-apiv3), pandas, pyarrow (parquet), pywebpush + py-vapid, plotly.

## Global Constraints

- Python 3.11+ (existing project floor).
- All new dependencies added to `requirements.txt` with pinned versions.
- All test code lives in `tests/` mirroring source path.
- Every commit passes `pytest tests/ -x` before push.
- Never introduce a signal-generating strategy without a walk-forward with honest costs (0.1% round-trip).
- Never soften or hide a losing measurement (per `feedback_trust_first_architecture.md` + `feedback_expectancy_over_winrate.md`).
- UI shows R-multiple expectancy as primary metric; win-rate is decomposition detail only.
- Live-price freshness is a HARD REQUIREMENT: trust badge must name source + freshness explicitly; never display lagged number as live.
- Commit messages use conventional format (`feat:`, `fix:`, `test:`, `chore:`, `docs:`); include reference to task ID (e.g., "T1.3").
- Signal pipeline must be idempotent: same inputs → same signal (hash-verified).

## Repository Layout

- **Code repo** (`nse-trading-lab`, public): all Python, Streamlit, workflows, tests.
- **Signals repo** (`nse-trading-lab-signals`, private): only `signals/`, `state/`, `paper_ledger/`, `equity_daily/` parquet + JSON files. Written by pipeline via deploy key.

## Plan Composition Note

This plan is authored in **loop-by-loop increments** to match the spec's shipping cadence and to allow measurement between loops. Loop 1 (below) is complete. Loops 2, 3, 3.5, 4 are appended to this same file in sequence after each preceding loop ships. See spec §9 for loop definitions.

---

# LOOP 1 — Pipeline + PWA + Regime Cockpit + First Signal E2E

**Deliverable:** Open the PWA on your phone during market hours, see the live tape regime, receive one Web Push notification per new SWING signal.

**Duration estimate:** ~1 week (15 tasks).

## L1 File Structure

Files created or modified in Loop 1:

**Code repo:**
- `pipeline/__init__.py` (new)
- `pipeline/fetch.py` (new) — Fyers + yfinance clients with fallback
- `pipeline/gates.py` (new) — staleness, dual-source, market-hours gates
- `pipeline/compute.py` (new) — orchestrates existing scorer/tape_monitor
- `pipeline/persist.py` (new) — writes JSON/parquet, git-pushes to signals repo
- `pipeline/push.py` (new) — VAPID Web Push dispatcher
- `pipeline/run.py` (new) — main entrypoint invoked by workflow
- `.github/workflows/signal-pipeline.yml` (new) — 5-min cron
- `.github/workflows/heartbeat.yml` (new) — 15-min self-check
- `pages/0_Today.py` (new) — replaces 1_Picks.py as landing
- `components/regime_cockpit.py` (new) — 4-cell top strip
- `components/trust_badge.py` (new) — pipeline health indicator
- `components/pwa_setup.py` (new) — manifest + service worker injection
- `static/manifest.json` (new) — PWA manifest
- `static/service-worker.js` (new) — push handler
- `Advanced/2_Dashboard.py`, `Advanced/10_Learn.py`, `Advanced/11_Settings.py` (moved from pages/)
- `requirements.txt` (modify — add fyers-apiv3, pywebpush, py-vapid, pyarrow)
- `tests/pipeline/test_fetch.py`, `test_gates.py`, `test_compute.py`, `test_persist.py`, `test_push.py` (new)
- `tests/components/test_regime_cockpit.py`, `test_trust_badge.py` (new)

**Signals repo (bootstrapped as empty):**
- `README.md`
- `state/latest.json` (initial stub)
- `state/pipeline_health.json` (initial stub)

## L1 Tasks

---

### Task 1.1: Create private signals repo + deploy key + GH secrets

**Files:**
- Create (in new signals repo): `README.md`
- Modify: GitHub repo settings (secrets)

**Interfaces:**
- Consumes: none
- Produces: `SIGNALS_DEPLOY_KEY` secret in code repo; empty private repo `nse-trading-lab-signals` accessible via deploy key

- [ ] **Step 1: Create private signals repo on GitHub**

```bash
gh repo create nse-trading-lab-signals --private --add-readme --description "Private signal + ledger store for nse-trading-lab pipeline"
```

Expected: repo URL returned.

- [ ] **Step 2: Generate deploy key pair locally**

```bash
ssh-keygen -t ed25519 -C "nse-trading-lab-pipeline" -f /tmp/nse_deploy_key -N ""
cat /tmp/nse_deploy_key.pub
```

Expected: public key printed.

- [ ] **Step 3: Add public key to signals repo as write-access deploy key**

```bash
gh repo deploy-key add /tmp/nse_deploy_key.pub -R "$(gh api user --jq .login)/nse-trading-lab-signals" --title "pipeline-writer" --allow-write
```

Expected: "✓ Deploy key added".

- [ ] **Step 4: Store private key + repo URL as secrets in code repo**

```bash
gh secret set SIGNALS_DEPLOY_KEY < /tmp/nse_deploy_key
gh secret set SIGNALS_REPO_URL --body "git@github.com:$(gh api user --jq .login)/nse-trading-lab-signals.git"
rm /tmp/nse_deploy_key /tmp/nse_deploy_key.pub
```

Expected: two secrets set.

- [ ] **Step 5: Bootstrap signals repo with initial state stubs**

```bash
git clone "git@github.com:$(gh api user --jq .login)/nse-trading-lab-signals.git" /tmp/signals-bootstrap
cd /tmp/signals-bootstrap
mkdir -p state signals paper_ledger equity_daily
echo '{"last_run_ts": null, "status": "not-yet-run", "errors": []}' > state/pipeline_health.json
echo '{"generated_at": null, "regime": null, "signals": []}' > state/latest.json
git add -A && git commit -m "chore: bootstrap state directories (T1.1)"
git push
cd - && rm -rf /tmp/signals-bootstrap
```

Expected: initial commit visible on signals repo main branch.

- [ ] **Step 6: Commit README note in code repo**

```bash
# In code repo
cat >> README.md <<'EOF'

## Signal pipeline
Live signals + trust ledger live in a separate private repo (`nse-trading-lab-signals`).
The pipeline writes there via a deploy key stored as `SIGNALS_DEPLOY_KEY` secret. See
`docs/superpowers/specs/2026-07-15-trustable-intraday-swing-redesign.md` §3 for architecture.
EOF
git add README.md
git commit -m "docs(readme): note signals repo split (T1.1)"
```

Expected: commit on main.

---

### Task 1.2: Skeleton GH Actions workflow — cron + repo checkout + hello-world

**Files:**
- Create: `.github/workflows/signal-pipeline.yml`

**Interfaces:**
- Consumes: `SIGNALS_DEPLOY_KEY`, `SIGNALS_REPO_URL` secrets
- Produces: workflow that runs every 5 min NSE hours, clones signals repo, prints hello, exits 0. Later tasks replace hello with real pipeline steps.

- [ ] **Step 1: Write the workflow file**

Create `.github/workflows/signal-pipeline.yml`:

```yaml
name: signal-pipeline
on:
  schedule:
    # NSE 09:15–15:30 IST = 03:45–10:00 UTC, Mon–Fri
    - cron: '*/5 3-10 * * 1-5'
  workflow_dispatch: {}

concurrency:
  group: signal-pipeline
  cancel-in-progress: false

jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 4
    steps:
      - name: Checkout code repo
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: pip

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Setup SSH for signals repo
        env:
          DEPLOY_KEY: ${{ secrets.SIGNALS_DEPLOY_KEY }}
        run: |
          mkdir -p ~/.ssh
          echo "$DEPLOY_KEY" > ~/.ssh/signals_key
          chmod 600 ~/.ssh/signals_key
          echo "Host github-signals" >> ~/.ssh/config
          echo "  HostName github.com" >> ~/.ssh/config
          echo "  IdentityFile ~/.ssh/signals_key" >> ~/.ssh/config
          echo "  StrictHostKeyChecking no" >> ~/.ssh/config

      - name: Clone signals repo
        env:
          SIGNALS_REPO_URL: ${{ secrets.SIGNALS_REPO_URL }}
        run: |
          # Rewrite SSH host to use our aliased key
          SIGNALS_SSH="${SIGNALS_REPO_URL/git@github.com/git@github-signals}"
          git clone "$SIGNALS_SSH" /tmp/signals

      - name: Hello world
        run: |
          echo "Pipeline hello — $(date -u +%FT%TZ)"
          ls -la /tmp/signals/state
```

- [ ] **Step 2: Trigger workflow manually to verify**

```bash
git add .github/workflows/signal-pipeline.yml
git commit -m "feat(pipeline): skeleton workflow with cron + repo clone (T1.2)"
git push
gh workflow run signal-pipeline.yml
sleep 10
gh run list --workflow=signal-pipeline.yml --limit 1
```

Expected: workflow appears in queue.

- [ ] **Step 3: Verify run succeeds**

```bash
RUN_ID=$(gh run list --workflow=signal-pipeline.yml --limit 1 --json databaseId --jq '.[0].databaseId')
gh run watch "$RUN_ID"
```

Expected: workflow completes green, hello-world message + signals/state directory listing visible in logs.

---

### Task 1.3: Fyers API client with token handling — write failing test

**Files:**
- Create: `pipeline/__init__.py`
- Create: `pipeline/fetch.py`
- Create: `tests/pipeline/__init__.py`
- Create: `tests/pipeline/test_fetch.py`
- Modify: `requirements.txt` (add `fyers-apiv3>=3.1.7`)

**Interfaces:**
- Consumes: `FYERS_APP_ID`, `FYERS_ACCESS_TOKEN` env vars
- Produces: `fetch_fyers_ltp(symbols: list[str]) -> dict[str, LTPQuote]` where `LTPQuote` is a dataclass `{symbol, ltp, ts, source}`; raises `FyersAuthError` on 401/403 (caller falls back to yfinance)

- [ ] **Step 1: Add dependency**

```bash
echo "fyers-apiv3>=3.1.7" >> requirements.txt
pip install fyers-apiv3
```

- [ ] **Step 2: Write the failing test**

Create `tests/pipeline/__init__.py` (empty) and `tests/pipeline/test_fetch.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from pipeline.fetch import fetch_fyers_ltp, LTPQuote, FyersAuthError


def test_fetch_fyers_ltp_returns_dict_of_quotes():
    fake_response = {
        "s": "ok",
        "d": [
            {"n": "NSE:RELIANCE-EQ", "v": {"lp": 2450.10, "tt": 1720000000}},
            {"n": "NSE:TCS-EQ",       "v": {"lp": 3820.50, "tt": 1720000000}},
        ],
    }
    with patch("pipeline.fetch._fyers_client") as mock_client:
        mock_client.return_value.quotes.return_value = fake_response
        result = fetch_fyers_ltp(["RELIANCE", "TCS"])
    assert set(result.keys()) == {"RELIANCE", "TCS"}
    assert isinstance(result["RELIANCE"], LTPQuote)
    assert result["RELIANCE"].ltp == 2450.10
    assert result["RELIANCE"].source == "fyers"


def test_fetch_fyers_ltp_raises_on_auth_failure():
    with patch("pipeline.fetch._fyers_client") as mock_client:
        mock_client.return_value.quotes.return_value = {"s": "error", "code": -300, "message": "invalid access token"}
        with pytest.raises(FyersAuthError):
            fetch_fyers_ltp(["RELIANCE"])
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest tests/pipeline/test_fetch.py -v
```

Expected: FAIL with "ImportError: cannot import name 'fetch_fyers_ltp'".

- [ ] **Step 4: Write minimal implementation**

Create `pipeline/__init__.py` (empty) and `pipeline/fetch.py`:

```python
from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from fyers_apiv3 import fyersModel


class FyersAuthError(RuntimeError):
    """Raised when Fyers API rejects credentials (401/403/invalid token)."""


@dataclass(frozen=True)
class LTPQuote:
    symbol: str
    ltp: float
    ts: datetime
    source: str  # "fyers" | "yfinance" | "nse"


def _fyers_client() -> fyersModel.FyersModel:
    app_id = os.environ["FYERS_APP_ID"]
    token = os.environ["FYERS_ACCESS_TOKEN"]
    return fyersModel.FyersModel(client_id=app_id, token=token, is_async=False)


def _to_fyers_symbol(sym: str) -> str:
    """Map raw NSE ticker to Fyers-namespaced form."""
    return f"NSE:{sym}-EQ"


def _from_fyers_symbol(fs: str) -> str:
    return fs.replace("NSE:", "").replace("-EQ", "")


def fetch_fyers_ltp(symbols: list[str]) -> dict[str, LTPQuote]:
    """Fetch last-traded price for a batch of NSE symbols via Fyers.

    Raises FyersAuthError on credential failure so caller can fall back to yfinance.
    Returns partial dict on partial success (never raises for missing symbols).
    """
    client = _fyers_client()
    fyers_syms = [_to_fyers_symbol(s) for s in symbols]
    response = client.quotes({"symbols": ",".join(fyers_syms)})
    if response.get("s") != "ok":
        code = response.get("code")
        msg = response.get("message", "")
        if code in (-300, -352) or "token" in msg.lower():
            raise FyersAuthError(msg)
        return {}
    out: dict[str, LTPQuote] = {}
    for entry in response.get("d", []):
        sym = _from_fyers_symbol(entry["n"])
        v = entry["v"]
        out[sym] = LTPQuote(
            symbol=sym,
            ltp=float(v["lp"]),
            ts=datetime.fromtimestamp(int(v["tt"]), tz=timezone.utc),
            source="fyers",
        )
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/pipeline/test_fetch.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add pipeline/ tests/pipeline/ requirements.txt
git commit -m "feat(pipeline): fyers LTP client with auth-error fallback signal (T1.3)"
```

---

### Task 1.4: yfinance fallback fetcher + primary/fallback orchestration

**Files:**
- Modify: `pipeline/fetch.py` (add `fetch_yfinance_ltp` + `fetch_ltp_with_fallback`)
- Modify: `tests/pipeline/test_fetch.py` (add tests)

**Interfaces:**
- Consumes: `fetch_fyers_ltp` from T1.3
- Produces: `fetch_yfinance_ltp(symbols) -> dict[str, LTPQuote]` (never raises, returns partial on failure); `fetch_ltp_with_fallback(symbols) -> tuple[dict[str, LTPQuote], str]` where second element is "fyers" | "yfinance" | "mixed" indicating which source served the data (for trust badge)

- [ ] **Step 1: Write failing tests**

Append to `tests/pipeline/test_fetch.py`:

```python
from pipeline.fetch import fetch_yfinance_ltp, fetch_ltp_with_fallback


def test_fetch_yfinance_ltp_returns_dict():
    fake_hist = MagicMock()
    fake_hist.tail.return_value = MagicMock()
    fake_row = MagicMock()
    fake_row.__getitem__ = lambda self, k: 2450.5 if k == "Close" else None
    fake_row.name = MagicMock()
    fake_row.name.to_pydatetime.return_value = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)
    with patch("pipeline.fetch.yf.download") as mock_dl:
        import pandas as pd
        mock_dl.return_value = pd.DataFrame(
            {"Close": [2450.5]},
            index=pd.DatetimeIndex(["2026-07-15 10:30"], tz="UTC"),
        )
        result = fetch_yfinance_ltp(["RELIANCE"])
    assert "RELIANCE" in result
    assert result["RELIANCE"].source == "yfinance"


def test_fetch_ltp_with_fallback_prefers_fyers():
    fyers_quote = {"RELIANCE": LTPQuote("RELIANCE", 2450.10, datetime.now(timezone.utc), "fyers")}
    with patch("pipeline.fetch.fetch_fyers_ltp", return_value=fyers_quote):
        result, source = fetch_ltp_with_fallback(["RELIANCE"])
    assert source == "fyers"
    assert result["RELIANCE"].ltp == 2450.10


def test_fetch_ltp_with_fallback_uses_yfinance_on_fyers_auth_fail():
    yf_quote = {"RELIANCE": LTPQuote("RELIANCE", 2451.00, datetime.now(timezone.utc), "yfinance")}
    with patch("pipeline.fetch.fetch_fyers_ltp", side_effect=FyersAuthError("bad token")):
        with patch("pipeline.fetch.fetch_yfinance_ltp", return_value=yf_quote):
            result, source = fetch_ltp_with_fallback(["RELIANCE"])
    assert source == "yfinance"
    assert result["RELIANCE"].source == "yfinance"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/pipeline/test_fetch.py -v
```

Expected: 3 new tests FAIL with import errors.

- [ ] **Step 3: Implement**

Append to `pipeline/fetch.py`:

```python
import yfinance as yf
import logging

_log = logging.getLogger(__name__)


def fetch_yfinance_ltp(symbols: list[str]) -> dict[str, LTPQuote]:
    """Fetch last close from yfinance 1-min bars (delayed ~15 min).

    Never raises — logs and returns partial dict on failure.
    """
    out: dict[str, LTPQuote] = {}
    for sym in symbols:
        try:
            df = yf.download(f"{sym}.NS", period="1d", interval="1m", progress=False, auto_adjust=False)
            if df is None or df.empty:
                continue
            last = df.tail(1).iloc[0]
            ts = last.name.to_pydatetime() if hasattr(last.name, "to_pydatetime") else datetime.now(timezone.utc)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out[sym] = LTPQuote(symbol=sym, ltp=float(last["Close"]), ts=ts, source="yfinance")
        except Exception as e:  # yfinance is flaky by design
            _log.warning("yfinance ltp failed for %s: %s", sym, e)
    return out


def fetch_ltp_with_fallback(symbols: list[str]) -> tuple[dict[str, LTPQuote], str]:
    """Primary: Fyers. Fallback: yfinance. Returns (quotes, source_label).

    source_label: "fyers" | "yfinance" | "mixed" (used by trust badge).
    """
    try:
        fy = fetch_fyers_ltp(symbols)
        if fy and len(fy) >= len(symbols) * 0.9:
            return fy, "fyers"
        missing = [s for s in symbols if s not in fy]
        if missing:
            yf_fill = fetch_yfinance_ltp(missing)
            merged = {**fy, **yf_fill}
            return merged, "mixed" if fy else "yfinance"
        return fy, "fyers"
    except FyersAuthError as e:
        _log.warning("Fyers auth failed, falling back to yfinance: %s", e)
        return fetch_yfinance_ltp(symbols), "yfinance"
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/pipeline/test_fetch.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/fetch.py tests/pipeline/test_fetch.py
git commit -m "feat(pipeline): yfinance fallback + primary/fallback orchestration (T1.4)"
```

---

### Task 1.5: Freshness/dual-source/market-hours gates

**Files:**
- Create: `pipeline/gates.py`
- Create: `tests/pipeline/test_gates.py`

**Interfaces:**
- Consumes: `LTPQuote` from T1.3
- Produces:
  - `is_market_hours(now: datetime) -> bool` — True during 09:15–15:30 IST Mon-Fri
  - `staleness_gate(quotes: dict[str, LTPQuote], max_age_min: int = 20) -> tuple[dict, list[str]]` — returns (fresh_quotes, stale_symbols)
  - `dual_source_gate(primary: dict[str, LTPQuote], reference: dict[str, LTPQuote], tolerance_pct: float = 0.5) -> list[str]` — returns list of symbols where sources diverge >tolerance

- [ ] **Step 1: Write failing tests**

Create `tests/pipeline/test_gates.py`:

```python
from datetime import datetime, timezone, timedelta
from pipeline.fetch import LTPQuote
from pipeline.gates import is_market_hours, staleness_gate, dual_source_gate

IST = timezone(timedelta(hours=5, minutes=30))


def test_market_hours_inside_window():
    inside = datetime(2026, 7, 15, 10, 30, tzinfo=IST)  # Wednesday 10:30 IST
    assert is_market_hours(inside) is True


def test_market_hours_before_open():
    before = datetime(2026, 7, 15, 9, 10, tzinfo=IST)
    assert is_market_hours(before) is False


def test_market_hours_weekend():
    sat = datetime(2026, 7, 18, 10, 30, tzinfo=IST)  # Saturday
    assert is_market_hours(sat) is False


def test_staleness_gate_filters_old():
    now = datetime.now(timezone.utc)
    fresh = LTPQuote("A", 100.0, now - timedelta(minutes=5), "fyers")
    stale = LTPQuote("B", 200.0, now - timedelta(minutes=25), "fyers")
    kept, dropped = staleness_gate({"A": fresh, "B": stale}, max_age_min=20)
    assert list(kept.keys()) == ["A"]
    assert dropped == ["B"]


def test_dual_source_gate_flags_divergence():
    now = datetime.now(timezone.utc)
    primary = {"RELIANCE": LTPQuote("RELIANCE", 2450.0, now, "fyers")}
    reference = {"RELIANCE": LTPQuote("RELIANCE", 2470.0, now, "yfinance")}  # ~0.8% off
    divergent = dual_source_gate(primary, reference, tolerance_pct=0.5)
    assert "RELIANCE" in divergent


def test_dual_source_gate_ignores_within_tolerance():
    now = datetime.now(timezone.utc)
    primary = {"RELIANCE": LTPQuote("RELIANCE", 2450.0, now, "fyers")}
    reference = {"RELIANCE": LTPQuote("RELIANCE", 2452.0, now, "yfinance")}  # ~0.08% off
    divergent = dual_source_gate(primary, reference, tolerance_pct=0.5)
    assert divergent == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/pipeline/test_gates.py -v
```

Expected: 6 failures (module not found).

- [ ] **Step 3: Implement**

Create `pipeline/gates.py`:

```python
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pipeline.fetch import LTPQuote

IST = timezone(timedelta(hours=5, minutes=30))


def is_market_hours(now: datetime) -> bool:
    """NSE cash market: 09:15–15:30 IST, Mon–Fri (no holiday awareness in v1)."""
    now_ist = now.astimezone(IST)
    if now_ist.weekday() >= 5:
        return False
    open_ = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    close_ = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_ <= now_ist <= close_


def staleness_gate(
    quotes: dict[str, LTPQuote],
    max_age_min: int = 20,
    now: datetime | None = None,
) -> tuple[dict[str, LTPQuote], list[str]]:
    """Split quotes into (fresh, list_of_stale_symbols)."""
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=max_age_min)
    fresh: dict[str, LTPQuote] = {}
    stale: list[str] = []
    for sym, q in quotes.items():
        if q.ts >= cutoff:
            fresh[sym] = q
        else:
            stale.append(sym)
    return fresh, stale


def dual_source_gate(
    primary: dict[str, LTPQuote],
    reference: dict[str, LTPQuote],
    tolerance_pct: float = 0.5,
) -> list[str]:
    """Return symbols where |primary - reference| / reference * 100 > tolerance_pct."""
    divergent: list[str] = []
    for sym, p_quote in primary.items():
        r_quote = reference.get(sym)
        if r_quote is None or r_quote.ltp <= 0:
            continue
        diff_pct = abs(p_quote.ltp - r_quote.ltp) / r_quote.ltp * 100
        if diff_pct > tolerance_pct:
            divergent.append(sym)
    return divergent
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/pipeline/test_gates.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/gates.py tests/pipeline/test_gates.py
git commit -m "feat(pipeline): freshness + dual-source + market-hours gates (T1.5)"
```

---

### Task 1.6: Compute step — call existing tape_monitor + swing scorer, produce SignalBatch

**Files:**
- Create: `pipeline/compute.py`
- Create: `tests/pipeline/test_compute.py`

**Interfaces:**
- Consumes: `LTPQuote` from T1.3; existing `nse_backtest.tape_monitor.assess_tape`; existing `nse_backtest.trading_modes.analyze_swing`
- Produces: `compute_signal_batch(ltps: dict[str, LTPQuote], nifty_df: pd.DataFrame) -> SignalBatch` where `SignalBatch` is a dataclass `{generated_at, regime, regime_conditions, swing_signals: list[Signal]}`; `Signal` dataclass matches spec §5.4 JSON keys (subset for Loop 1: signal_id, mode, action, symbol, entry, stop_loss, target, tape_regime, thesis)

- [ ] **Step 1: Write failing test**

Create `tests/pipeline/test_compute.py`:

```python
import pandas as pd
from datetime import datetime, timezone
from unittest.mock import patch
from pipeline.fetch import LTPQuote
from pipeline.compute import compute_signal_batch, SignalBatch, Signal


def _fake_nifty_df():
    idx = pd.date_range("2024-01-01", periods=400, freq="B")
    return pd.DataFrame({"Close": range(21000, 21000 + 400)}, index=idx)


def test_compute_returns_signal_batch_with_regime():
    ltps = {"RELIANCE": LTPQuote("RELIANCE", 2450.0, datetime.now(timezone.utc), "fyers")}
    with patch("pipeline.compute.assess_tape") as mock_tape:
        mock_tape.return_value.regime = "MIXED"
        mock_tape.return_value.nifty_close = 21400.0
        mock_tape.return_value.recommendation = "selective"
        mock_tape.return_value.return_60d_pct = 3.2
        mock_tape.return_value.ema_200_slope_pct_20d = 0.15
        with patch("pipeline.compute._analyze_symbol") as mock_analyze:
            mock_analyze.return_value = Signal(
                signal_id="swing-2026-07-15-RELIANCE-1035",
                mode="SWING", action="BUY", symbol="RELIANCE",
                entry=2450.0, stop_loss=2410.0, target=2530.0,
                tape_regime="MIXED", thesis="test",
            )
            batch = compute_signal_batch(ltps, _fake_nifty_df())
    assert isinstance(batch, SignalBatch)
    assert batch.regime == "MIXED"
    assert len(batch.swing_signals) == 1
    assert batch.swing_signals[0].symbol == "RELIANCE"
```

- [ ] **Step 2: Run test to verify fail**

```bash
pytest tests/pipeline/test_compute.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

Create `pipeline/compute.py`:

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import pandas as pd
from pipeline.fetch import LTPQuote
from nse_backtest.tape_monitor import assess_tape


@dataclass(frozen=True)
class Signal:
    signal_id: str
    mode: str            # "SWING" | "INTRADAY"
    action: str          # "BUY" | "SELL" | "EXIT"
    symbol: str
    entry: float
    stop_loss: float
    target: float
    tape_regime: str
    thesis: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class SignalBatch:
    generated_at: datetime
    regime: str
    regime_conditions: dict
    swing_signals: list[Signal]


def _analyze_symbol(symbol: str, ltp: float, tape) -> Optional[Signal]:
    """Wrapper around existing analyze_swing — Loop 1 emits a stub signal based
    on tape regime only. Real scorer integration lands in Loop 2 (T2.x)."""
    if tape.regime == "HOSTILE":
        return None
    sl = round(ltp * 0.98, 2)
    tgt = round(ltp * 1.03, 2)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    return Signal(
        signal_id=f"swing-{ts}-{symbol}",
        mode="SWING", action="BUY", symbol=symbol,
        entry=ltp, stop_loss=sl, target=tgt,
        tape_regime=tape.regime,
        thesis=f"L1 stub — tape regime {tape.regime}",
    )


def compute_signal_batch(
    ltps: dict[str, LTPQuote],
    nifty_df: pd.DataFrame,
) -> SignalBatch:
    tape = assess_tape(nifty_df)
    signals: list[Signal] = []
    for sym, quote in ltps.items():
        s = _analyze_symbol(sym, quote.ltp, tape)
        if s is not None:
            signals.append(s)
    conditions = {
        "nifty_close": tape.nifty_close,
        "return_60d_pct": tape.return_60d_pct,
        "ema_200_slope_pct_20d": tape.ema_200_slope_pct_20d,
    }
    return SignalBatch(
        generated_at=datetime.now(timezone.utc),
        regime=tape.regime,
        regime_conditions=conditions,
        swing_signals=signals,
    )
```

- [ ] **Step 4: Run test**

```bash
pytest tests/pipeline/test_compute.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/compute.py tests/pipeline/test_compute.py
git commit -m "feat(pipeline): compute_signal_batch with tape regime + stub swing signal (T1.6)"
```

---

### Task 1.7: Persist step — write JSON state + push to signals repo

**Files:**
- Create: `pipeline/persist.py`
- Create: `tests/pipeline/test_persist.py`

**Interfaces:**
- Consumes: `SignalBatch` from T1.6
- Produces: `persist_batch(batch: SignalBatch, signals_repo_path: Path) -> None` — writes `state/latest.json` and `state/pipeline_health.json`, appends `signals/YYYY-MM-DD/HHMM.json`, git-commits + pushes. Also `write_health(path, status, errors, last_run_ts) -> None` for heartbeat callers.

- [ ] **Step 1: Write failing test**

Create `tests/pipeline/test_persist.py`:

```python
import json
from pathlib import Path
from datetime import datetime, timezone
import pytest
from pipeline.compute import SignalBatch, Signal
from pipeline.persist import persist_batch, write_health


def _batch():
    return SignalBatch(
        generated_at=datetime(2026, 7, 15, 10, 35, tzinfo=timezone.utc),
        regime="MIXED",
        regime_conditions={"nifty_close": 21400.0},
        swing_signals=[
            Signal("swing-t1", "SWING", "BUY", "RELIANCE", 2450.0, 2410.0, 2530.0, "MIXED", "test")
        ],
    )


def test_persist_writes_latest_json(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "signals").mkdir()
    persist_batch(_batch(), tmp_path, push=False)
    latest = json.loads((tmp_path / "state" / "latest.json").read_text())
    assert latest["regime"] == "MIXED"
    assert latest["signals"][0]["symbol"] == "RELIANCE"


def test_persist_appends_daily_signal_log(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "signals").mkdir()
    persist_batch(_batch(), tmp_path, push=False)
    daily_dir = tmp_path / "signals" / "2026-07-15"
    assert daily_dir.exists()
    files = list(daily_dir.glob("*.json"))
    assert len(files) == 1


def test_write_health_updates_status(tmp_path):
    health_path = tmp_path / "pipeline_health.json"
    write_health(health_path, status="healthy", errors=[], last_run_ts=datetime.now(timezone.utc))
    h = json.loads(health_path.read_text())
    assert h["status"] == "healthy"
```

- [ ] **Step 2: Run test to verify fail**

```bash
pytest tests/pipeline/test_persist.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

Create `pipeline/persist.py`:

```python
from __future__ import annotations
import json
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from pipeline.compute import SignalBatch


def _signal_to_dict(s) -> dict:
    d = asdict(s)
    d["generated_at"] = s.generated_at.isoformat()
    return d


def _batch_to_dict(batch: SignalBatch) -> dict:
    return {
        "generated_at": batch.generated_at.isoformat(),
        "regime": batch.regime,
        "regime_conditions": batch.regime_conditions,
        "signals": [_signal_to_dict(s) for s in batch.swing_signals],
    }


def persist_batch(batch: SignalBatch, repo_path: Path, push: bool = True) -> None:
    """Write latest.json + append daily audit log + (optionally) git-push."""
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
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "status": status,
        "errors": errors,
        "last_run_ts": last_run_ts.isoformat(),
    }, indent=2))


def _git_commit_and_push(repo_path: Path, msg: str) -> None:
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
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/pipeline/test_persist.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/persist.py tests/pipeline/test_persist.py
git commit -m "feat(pipeline): persist_batch writes state + audit log + git push (T1.7)"
```

---

### Task 1.8: Main entrypoint + wire full pipeline into workflow

**Files:**
- Create: `pipeline/run.py`
- Modify: `.github/workflows/signal-pipeline.yml` (replace hello-world with `python -m pipeline.run`)

**Interfaces:**
- Consumes: T1.3–T1.7 (fetch, gates, compute, persist)
- Produces: CLI entrypoint that when run does: fetch LTPs → gate → compute batch → persist → write health. Exits nonzero on unrecoverable errors.

- [ ] **Step 1: Write the entrypoint**

Create `pipeline/run.py`:

```python
from __future__ import annotations
import os
import sys
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from pipeline.fetch import fetch_ltp_with_fallback
from pipeline.gates import is_market_hours, staleness_gate
from pipeline.compute import compute_signal_batch
from pipeline.persist import persist_batch, write_health
from nse_backtest.data import fetch_nifty50, NIFTY50_SYMBOLS


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pipeline.run")


def main() -> int:
    signals_path = Path(os.environ.get("SIGNALS_REPO_PATH", "/tmp/signals"))
    health_path = signals_path / "state" / "pipeline_health.json"
    now = datetime.now(timezone.utc)
    errors: list[str] = []

    if not is_market_hours(now):
        log.info("Outside market hours — skipping compute, updating heartbeat only")
        write_health(health_path, "idle-outside-hours", [], now)
        persist_health_only(signals_path)
        return 0

    try:
        log.info("Fetching LTPs for %d symbols…", len(NIFTY50_SYMBOLS))
        ltps, source = fetch_ltp_with_fallback(NIFTY50_SYMBOLS)
        log.info("Got %d LTPs from source=%s", len(ltps), source)

        fresh, stale = staleness_gate(ltps, max_age_min=20, now=now)
        if stale:
            errors.append(f"stale: {stale[:5]}{'…' if len(stale)>5 else ''}")
            log.warning("Stale symbols dropped: %s", stale)

        log.info("Fetching Nifty daily bars for regime…")
        nifty_df = fetch_nifty50(start="2022-01-01")

        batch = compute_signal_batch(fresh, nifty_df)
        log.info("Regime: %s · %d signals", batch.regime, len(batch.swing_signals))

        persist_batch(batch, signals_path, push=True)
        write_health(health_path, "healthy" if not errors else "degraded", errors, now)
        persist_health_only(signals_path)
        return 0
    except Exception as e:
        log.exception("Pipeline crashed")
        errors.append(f"crash: {type(e).__name__}: {e}")
        write_health(health_path, "degraded", errors, now)
        persist_health_only(signals_path)
        return 1


def persist_health_only(repo_path: Path) -> None:
    """Push just the health file when compute was skipped or failed."""
    import subprocess
    subprocess.run(["git", "-C", str(repo_path), "add", "state/pipeline_health.json"], check=False)
    result = subprocess.run(["git", "-C", str(repo_path), "diff", "--cached", "--quiet"], check=False)
    if result.returncode == 0:
        return
    subprocess.check_call([
        "git", "-C", str(repo_path),
        "-c", "user.email=pipeline@nse-trading-lab", "-c", "user.name=nse-pipeline",
        "commit", "-m", "chore(pipeline): health heartbeat",
    ])
    subprocess.check_call(["git", "-C", str(repo_path), "push", "origin", "HEAD:main"])


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Update workflow to invoke pipeline**

Modify `.github/workflows/signal-pipeline.yml` — replace the "Hello world" step with:

```yaml
      - name: Run pipeline
        env:
          FYERS_APP_ID: ${{ secrets.FYERS_APP_ID }}
          FYERS_ACCESS_TOKEN: ${{ secrets.FYERS_ACCESS_TOKEN }}
          SIGNALS_REPO_PATH: /tmp/signals
        run: python -m pipeline.run
```

- [ ] **Step 3: Add Fyers secrets to code repo**

```bash
gh secret set FYERS_APP_ID --body "<user provides>"
gh secret set FYERS_ACCESS_TOKEN --body "<user provides>"
```

(Human step — user obtains from Fyers dev console before running.)

- [ ] **Step 4: Commit + trigger**

```bash
git add pipeline/run.py .github/workflows/signal-pipeline.yml
git commit -m "feat(pipeline): main entrypoint + workflow integration (T1.8)"
git push
gh workflow run signal-pipeline.yml
```

- [ ] **Step 5: Verify signals repo receives a commit**

```bash
sleep 60
gh run list --workflow=signal-pipeline.yml --limit 1
# Then in signals repo:
gh api "repos/$(gh api user --jq .login)/nse-trading-lab-signals/commits?per_page=3" --jq '.[].commit.message'
```

Expected: at least one new "chore(pipeline)" commit.

---

### Task 1.9: Today page skeleton reading state/latest.json from signals repo

**Files:**
- Create: `pages/0_Today.py`
- Create: `components/state_reader.py`
- Create: `tests/components/test_state_reader.py`

**Interfaces:**
- Consumes: state file paths from env var `SIGNALS_LOCAL_PATH` (points to a local clone of signals repo; UI process must keep this cloned + `git pull`ed)
- Produces: `read_latest() -> dict | None` (returns None if file missing); `read_health() -> dict | None`. Today page reads both and renders regime + signal count.

- [ ] **Step 1: Write test**

Create `tests/components/__init__.py` (empty) and `tests/components/test_state_reader.py`:

```python
import json
from pathlib import Path
from components.state_reader import read_latest, read_health


def test_read_latest_returns_dict(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "latest.json").write_text(json.dumps({"regime": "MIXED", "signals": []}))
    monkeypatch.setenv("SIGNALS_LOCAL_PATH", str(tmp_path))
    assert read_latest()["regime"] == "MIXED"


def test_read_latest_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNALS_LOCAL_PATH", str(tmp_path))
    assert read_latest() is None


def test_read_health_returns_dict(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "pipeline_health.json").write_text(json.dumps({"status": "healthy"}))
    monkeypatch.setenv("SIGNALS_LOCAL_PATH", str(tmp_path))
    assert read_health()["status"] == "healthy"
```

- [ ] **Step 2: Run test to verify fail**

```bash
pytest tests/components/test_state_reader.py -v
```

- [ ] **Step 3: Implement state reader**

Create `components/state_reader.py`:

```python
from __future__ import annotations
import json
import os
from pathlib import Path


def _base() -> Path:
    return Path(os.environ.get("SIGNALS_LOCAL_PATH", str(Path.home() / ".nse-trading-lab" / "signals-clone")))


def read_latest() -> dict | None:
    p = _base() / "state" / "latest.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def read_health() -> dict | None:
    p = _base() / "state" / "pipeline_health.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())
```

- [ ] **Step 4: Write minimal Today page**

Create `pages/0_Today.py`:

```python
import streamlit as st
from components import theme, state
from components.state_reader import read_latest, read_health

st.set_page_config(page_title="Today | Trading Lab", page_icon="🎯", layout="wide")
st.markdown(theme.inject_css(), unsafe_allow_html=True)
state.init_session()

st.markdown("# 🎯 Today")

health = read_health()
if health is None:
    st.error("Pipeline state not found — the signals repo isn't cloned locally. Run `scripts/clone_signals.sh` once.")
    st.stop()

latest = read_latest()
if latest is None:
    st.warning("Pipeline hasn't produced its first batch yet.")
    st.stop()

st.caption(f"Pipeline status: **{health.get('status')}** · Last run: `{health.get('last_run_ts')}`")
st.markdown(f"### Tape regime: **{latest.get('regime')}**")
st.markdown(f"Signals in this batch: **{len(latest.get('signals', []))}**")

for s in latest.get("signals", []):
    st.markdown(f"- **{s['symbol']}** · {s['action']} @ ₹{s['entry']:.2f} · SL {s['stop_loss']:.2f} · Tgt {s['target']:.2f}")
```

- [ ] **Step 5: Add clone helper**

Create `scripts/clone_signals.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
DEST="${HOME}/.nse-trading-lab/signals-clone"
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" pull --ff-only
else
  mkdir -p "$(dirname "$DEST")"
  git clone "git@github.com:$(gh api user --jq .login)/nse-trading-lab-signals.git" "$DEST"
fi
```

Make executable + document:

```bash
chmod +x scripts/clone_signals.sh
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/components/test_state_reader.py -v
```

Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add pages/0_Today.py components/state_reader.py tests/components/ scripts/clone_signals.sh
git commit -m "feat(ui): Today page skeleton reads latest.json from signals repo (T1.9)"
```

---

### Task 1.10: Regime cockpit — 4-cell top strip

**Files:**
- Create: `components/regime_cockpit.py`
- Create: `tests/components/test_regime_cockpit.py`
- Modify: `pages/0_Today.py`

**Interfaces:**
- Consumes: `latest.json` dict from T1.9
- Produces: `render_cockpit(latest: dict, vix: float | None = None, breadth_pct: float | None = None) -> None` — renders 4-cell strip via `st.columns(4)`

- [ ] **Step 1: Write test** (validates HTML output structure)

Create `tests/components/test_regime_cockpit.py`:

```python
from components.regime_cockpit import build_cockpit_html


def test_cockpit_html_contains_all_four_cells():
    html = build_cockpit_html(regime="MIXED", nifty_close=21400.0, vix=14.2, breadth_pct=62.0, ema_slope=0.15)
    assert "TAPE" in html and "MIXED" in html
    assert "VIX" in html and "14.2" in html
    assert "BREADTH" in html and "62" in html
    assert "200EMA" in html
```

- [ ] **Step 2: Implement**

Create `components/regime_cockpit.py`:

```python
from __future__ import annotations
import streamlit as st

_REGIME_COLOR = {"TRENDING": "#00FF87", "MIXED": "#FFB800", "HOSTILE": "#FF4D4D"}


def build_cockpit_html(regime: str, nifty_close: float, vix: float | None,
                       breadth_pct: float | None, ema_slope: float | None) -> str:
    color = _REGIME_COLOR.get(regime, "#5A7390")
    vix_txt = f"{vix:.1f}" if vix is not None else "—"
    br_txt = f"{breadth_pct:.0f}%" if breadth_pct is not None else "—"
    slope_txt = f"{ema_slope:+.2f}%/20d" if ema_slope is not None else "—"
    return f"""
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px">
      <div style="border:2px solid {color};border-radius:10px;padding:12px;background:#0D1526">
        <div style="font-size:11px;color:#7A93AA">TAPE</div>
        <div style="font-size:20px;font-weight:700;color:{color}">{regime}</div>
        <div style="font-size:12px;color:#C9D5E0">Nifty {nifty_close:,.0f}</div>
      </div>
      <div style="border:1px solid #1E3A5F;border-radius:10px;padding:12px;background:#0D1526">
        <div style="font-size:11px;color:#7A93AA">INDIA VIX</div>
        <div style="font-size:20px;font-weight:700">{vix_txt}</div>
      </div>
      <div style="border:1px solid #1E3A5F;border-radius:10px;padding:12px;background:#0D1526">
        <div style="font-size:11px;color:#7A93AA">BREADTH</div>
        <div style="font-size:20px;font-weight:700">{br_txt}</div>
      </div>
      <div style="border:1px solid #1E3A5F;border-radius:10px;padding:12px;background:#0D1526">
        <div style="font-size:11px;color:#7A93AA">NIFTY vs 200EMA</div>
        <div style="font-size:20px;font-weight:700">{slope_txt}</div>
      </div>
    </div>
    """


def render_cockpit(latest: dict, vix: float | None = None, breadth_pct: float | None = None) -> None:
    regime = latest.get("regime", "UNKNOWN")
    conds = latest.get("regime_conditions", {})
    st.markdown(
        build_cockpit_html(
            regime=regime,
            nifty_close=conds.get("nifty_close", 0.0),
            vix=vix,
            breadth_pct=breadth_pct,
            ema_slope=conds.get("ema_200_slope_pct_20d"),
        ),
        unsafe_allow_html=True,
    )
```

- [ ] **Step 3: Wire into Today page**

Modify `pages/0_Today.py` — insert after the `st.markdown("# 🎯 Today")` line and before the `health = read_health()` line:

```python
from components.regime_cockpit import render_cockpit
```

And after `latest = read_latest()`, replace the plain regime text with:

```python
render_cockpit(latest)
st.markdown("---")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/components/test_regime_cockpit.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add components/regime_cockpit.py tests/components/test_regime_cockpit.py pages/0_Today.py
git commit -m "feat(ui): 4-cell regime cockpit on Today (T1.10)"
```

---

### Task 1.11: Trust badge — pipeline health indicator

**Files:**
- Create: `components/trust_badge.py`
- Create: `tests/components/test_trust_badge.py`
- Modify: `pages/0_Today.py`

**Interfaces:**
- Consumes: `health` dict from T1.9 state reader
- Produces: `classify(health: dict, now: datetime) -> tuple[str, str]` returning `(state, message)` where state ∈ `{"healthy","degraded","dead"}`; `render_badge(health, now, source_label)`

- [ ] **Step 1: Write test**

Create `tests/components/test_trust_badge.py`:

```python
from datetime import datetime, timezone, timedelta
from components.trust_badge import classify

NOW = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)


def test_healthy_recent_run_no_errors():
    h = {"status": "healthy", "errors": [], "last_run_ts": (NOW - timedelta(minutes=3)).isoformat()}
    state, msg = classify(h, NOW)
    assert state == "healthy"


def test_degraded_when_errors_present():
    h = {"status": "degraded", "errors": ["stale: [X]"], "last_run_ts": (NOW - timedelta(minutes=3)).isoformat()}
    state, _ = classify(h, NOW)
    assert state == "degraded"


def test_degraded_when_10_to_30_min_old():
    h = {"status": "healthy", "errors": [], "last_run_ts": (NOW - timedelta(minutes=15)).isoformat()}
    state, _ = classify(h, NOW)
    assert state == "degraded"


def test_dead_when_over_30_min_old():
    h = {"status": "healthy", "errors": [], "last_run_ts": (NOW - timedelta(minutes=45)).isoformat()}
    state, _ = classify(h, NOW)
    assert state == "dead"
```

- [ ] **Step 2: Implement**

Create `components/trust_badge.py`:

```python
from __future__ import annotations
from datetime import datetime, timedelta
import streamlit as st


def classify(health: dict, now: datetime) -> tuple[str, str]:
    ts_str = health.get("last_run_ts")
    if ts_str is None:
        return "dead", "Pipeline has never run"
    last = datetime.fromisoformat(ts_str)
    age = now - last
    if age > timedelta(minutes=30):
        return "dead", f"Last run {int(age.total_seconds()/60)} min ago — DO NOT TRADE off cached signals"
    if age > timedelta(minutes=10) or health.get("errors"):
        errs = health.get("errors", [])
        why = f" ({errs[0]})" if errs else ""
        return "degraded", f"Last run {int(age.total_seconds()/60)} min ago{why}"
    return "healthy", f"Last run {int(age.total_seconds()/60)} min ago"


def render_badge(health: dict, now: datetime, source_label: str = "yfinance") -> None:
    state, msg = classify(health, now)
    color = {"healthy": "#00FF87", "degraded": "#FFB800", "dead": "#FF4D4D"}[state]
    dot = {"healthy": "🟢", "degraded": "🟡", "dead": "🔴"}[state]
    prices_line = (
        f"Live prices ON ({source_label})" if source_label == "fyers"
        else f"⚠ Live prices degraded ({source_label} — showing 15-min lag)"
    )
    st.markdown(
        f'<div style="border:2px solid {color};border-radius:10px;padding:8px 14px;background:#0D1526;'
        f'margin:0 0 12px 0;font-size:13px;color:#C9D5E0">'
        f'<b>{dot} Pipeline {state}</b> · {msg} · {prices_line}'
        f'</div>',
        unsafe_allow_html=True,
    )
```

- [ ] **Step 3: Extend latest.json + persist to include source_label**

Modify `pipeline/persist.py` — in `_batch_to_dict`, add `"quote_source": batch.regime_conditions.get("quote_source", "unknown")`.

Modify `pipeline/compute.py` — accept optional `quote_source: str` and set it in `regime_conditions`:

```python
def compute_signal_batch(ltps, nifty_df, quote_source: str = "yfinance"):
    # ...
    conditions["quote_source"] = quote_source
```

Modify `pipeline/run.py` to thread `source` from `fetch_ltp_with_fallback` into `compute_signal_batch`.

- [ ] **Step 4: Wire badge into Today page**

Modify `pages/0_Today.py` — after `latest = read_latest()`, insert:

```python
from components.trust_badge import render_badge
from datetime import datetime, timezone
render_badge(health, datetime.now(timezone.utc),
             source_label=latest.get("regime_conditions", {}).get("quote_source", "yfinance"))
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/components/test_trust_badge.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add components/trust_badge.py tests/components/test_trust_badge.py pages/0_Today.py pipeline/persist.py pipeline/compute.py pipeline/run.py
git commit -m "feat(ui): trust badge with pipeline health + quote source (T1.11)"
```

---

### Task 1.12: PWA manifest + service worker + install path

**Files:**
- Create: `static/manifest.json`
- Create: `static/service-worker.js`
- Create: `components/pwa_setup.py`
- Modify: `pages/0_Today.py`

**Interfaces:**
- Consumes: none
- Produces: `inject_pwa()` renders `<link rel="manifest">` + service-worker registration `<script>` into Streamlit page head via `st.markdown` unsafe_allow_html

- [ ] **Step 1: Create manifest**

Create `static/manifest.json`:

```json
{
  "name": "NSE Trading Lab",
  "short_name": "NSE Lab",
  "description": "Trustable intraday + swing signals",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0D1526",
  "theme_color": "#00FF87",
  "icons": [
    {
      "src": "https://em-content.zobj.net/thumbs/240/apple/354/direct-hit_1f3af.png",
      "sizes": "192x192",
      "type": "image/png"
    },
    {
      "src": "https://em-content.zobj.net/thumbs/240/apple/354/direct-hit_1f3af.png",
      "sizes": "512x512",
      "type": "image/png"
    }
  ]
}
```

- [ ] **Step 2: Create service worker**

Create `static/service-worker.js`:

```javascript
// nse-trading-lab service worker — handles push notifications
self.addEventListener('push', (event) => {
  if (!event.data) return;
  let payload;
  try { payload = event.data.json(); } catch { payload = { title: 'NSE Lab', body: event.data.text() }; }
  const opts = {
    body: payload.body || '',
    icon: 'https://em-content.zobj.net/thumbs/240/apple/354/direct-hit_1f3af.png',
    badge: 'https://em-content.zobj.net/thumbs/240/apple/354/direct-hit_1f3af.png',
    data: payload.data || {},
    tag: payload.tag || 'nse-lab-signal',
  };
  event.waitUntil(self.registration.showNotification(payload.title || 'NSE Lab', opts));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data.url || '/';
  event.waitUntil(clients.openWindow(url));
});
```

- [ ] **Step 3: Create PWA injector**

Create `components/pwa_setup.py`:

```python
import streamlit as st


def inject_pwa() -> None:
    """Inject PWA manifest link + service-worker registration script.

    Streamlit doesn't natively serve static/, so this uses raw.githubusercontent
    URLs after the code repo is pushed. Local dev: point MANIFEST_URL env var
    to a local server.
    """
    import os
    base = os.environ.get(
        "PWA_STATIC_BASE",
        "https://raw.githubusercontent.com/USER/nse-trading-lab/main/static",
    )
    st.markdown(
        f'''
        <link rel="manifest" href="{base}/manifest.json">
        <meta name="theme-color" content="#00FF87">
        <script>
        if ('serviceWorker' in navigator) {{
          navigator.serviceWorker.register('{base}/service-worker.js')
            .then(reg => console.log('SW registered', reg.scope))
            .catch(err => console.warn('SW failed', err));
        }}
        </script>
        ''',
        unsafe_allow_html=True,
    )
```

- [ ] **Step 4: Wire into Today page**

Modify `pages/0_Today.py` — add near the top after `set_page_config`:

```python
from components.pwa_setup import inject_pwa
inject_pwa()
```

- [ ] **Step 5: Manual verification**

Run Streamlit locally. Open in Chrome. DevTools → Application → Manifest — verify manifest loaded. Application → Service Workers — verify registered.

- [ ] **Step 6: Commit**

```bash
git add static/ components/pwa_setup.py pages/0_Today.py
git commit -m "feat(ui): PWA manifest + service worker registration (T1.12)"
```

---

### Task 1.13: VAPID keys + Web Push subscription flow

**Files:**
- Create: `pipeline/push.py`
- Create: `tests/pipeline/test_push.py`
- Modify: `static/service-worker.js` (no change, just verify)
- Create: `pages/Advanced/11_Settings.py` (or update if it exists) — add "Enable notifications" button
- Modify: `requirements.txt` (add `pywebpush>=1.14.0`, `py-vapid>=1.9.0`)

**Interfaces:**
- Consumes: VAPID keys from `VAPID_PRIVATE_KEY` + `VAPID_PUBLIC_KEY` env vars; push subscriptions stored in signals repo at `state/push_subscriptions.json`
- Produces: `send_push(payload: dict, subscriptions: list[dict]) -> list[str]` returns list of failed endpoints; UI helper `save_subscription(sub: dict)` appends to file

- [ ] **Step 1: Add dependencies + generate VAPID keys**

```bash
echo "pywebpush>=1.14.0" >> requirements.txt
echo "py-vapid>=1.9.0" >> requirements.txt
pip install pywebpush py-vapid
python -c "from py_vapid import Vapid; v = Vapid(); v.generate_keys(); v.save_key('/tmp/vapid_private.pem'); v.save_public_key('/tmp/vapid_public.pem'); print('Public key (base64url):'); import base64; from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat; pub = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint); print(base64.urlsafe_b64encode(pub).decode().rstrip('='))"
```

Copy the printed public key + private PEM into secrets:

```bash
gh secret set VAPID_PRIVATE_KEY < /tmp/vapid_private.pem
gh secret set VAPID_PUBLIC_KEY --body "<paste the base64url from above>"
gh secret set VAPID_CONTACT --body "mailto:gaurav.kumar@loglass.co.jp"
rm /tmp/vapid_private.pem /tmp/vapid_public.pem
```

- [ ] **Step 2: Write test**

Create `tests/pipeline/test_push.py`:

```python
from unittest.mock import patch
from pipeline.push import send_push


def test_send_push_returns_empty_on_all_success():
    subs = [{"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "x", "auth": "y"}}]
    with patch("pipeline.push.webpush") as mock_wp:
        mock_wp.return_value = None
        failed = send_push({"title": "T", "body": "x"}, subs)
    assert failed == []


def test_send_push_returns_failed_endpoints():
    subs = [{"endpoint": "https://fcm.googleapis.com/fcm/send/dead", "keys": {"p256dh": "x", "auth": "y"}}]
    with patch("pipeline.push.webpush", side_effect=RuntimeError("410 Gone")):
        failed = send_push({"title": "T", "body": "x"}, subs)
    assert failed == ["https://fcm.googleapis.com/fcm/send/dead"]
```

- [ ] **Step 3: Implement**

Create `pipeline/push.py`:

```python
from __future__ import annotations
import json
import logging
import os
from pywebpush import webpush, WebPushException

log = logging.getLogger(__name__)


def send_push(payload: dict, subscriptions: list[dict]) -> list[str]:
    """Send a push to every subscription. Returns list of failed endpoints
    (410 Gone or auth errors — caller should prune them from storage)."""
    priv = os.environ.get("VAPID_PRIVATE_KEY_PEM")
    contact = os.environ.get("VAPID_CONTACT", "mailto:nobody@example.com")
    if priv is None:
        # Fallback for local dev — read from file path
        priv_path = os.environ.get("VAPID_PRIVATE_KEY_PATH")
        if priv_path:
            with open(priv_path) as f:
                priv = f.read()
    if priv is None:
        log.error("No VAPID private key configured — push disabled")
        return [s["endpoint"] for s in subscriptions]

    failed: list[str] = []
    for sub in subscriptions:
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps(payload),
                vapid_private_key=priv,
                vapid_claims={"sub": contact},
                ttl=300,
            )
        except (WebPushException, RuntimeError) as e:
            log.warning("Push failed for %s: %s", sub["endpoint"][:40], e)
            failed.append(sub["endpoint"])
    return failed
```

- [ ] **Step 4: Add subscription UI in Settings (or Advanced/Settings)**

Create/modify `pages/Advanced/11_Settings.py` — append:

```python
import streamlit as st, os, json
from pathlib import Path

st.markdown("### 🔔 Notifications")
public_key = os.environ.get("VAPID_PUBLIC_KEY", "PUT-YOUR-PUBLIC-KEY-HERE")
st.markdown(
    f"""
    <button id="enable-push" style="background:#00FF87;color:#000;padding:10px 18px;border-radius:8px;border:0;font-weight:700">
      Enable push notifications
    </button>
    <div id="push-status" style="margin-top:8px;color:#7A93AA"></div>
    <script>
    const VAPID_PUB = '{public_key}';
    function urlBase64ToUint8Array(base64String) {{
      const padding = '='.repeat((4 - base64String.length % 4) % 4);
      const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
      const raw = atob(base64);
      const output = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; ++i) output[i] = raw.charCodeAt(i);
      return output;
    }}
    document.getElementById('enable-push').onclick = async () => {{
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') {{ document.getElementById('push-status').innerText = 'Permission denied'; return; }}
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({{
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(VAPID_PUB),
      }});
      // POST the subscription to a paste-bin file (user pastes into signals repo manually in v1)
      document.getElementById('push-status').innerText =
        'Copy this JSON into signals-repo/state/push_subscriptions.json:\\n\\n' + JSON.stringify(sub);
    }};
    </script>
    """,
    unsafe_allow_html=True,
)
```

(Note: Loop 1 uses manual paste; Loop 2 automates subscription persistence.)

- [ ] **Step 5: Run tests**

```bash
pytest tests/pipeline/test_push.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add pipeline/push.py tests/pipeline/test_push.py pages/Advanced/11_Settings.py requirements.txt
git commit -m "feat(pipeline): VAPID web-push dispatcher + subscription UI (T1.13)"
```

---

### Task 1.14: First signal E2E — pipeline pushes, PWA notifies

**Files:**
- Modify: `pipeline/run.py` (call `send_push` after `persist_batch` if new signals)

**Interfaces:**
- Consumes: T1.13 push dispatcher; T1.7 persist; T1.6 compute
- Produces: pipeline now sends one push per new signal at the end of each 5-min tick

- [ ] **Step 1: Extend pipeline/run.py to read subscriptions + push**

Modify `pipeline/run.py` — after `persist_batch(batch, signals_path, push=True)` insert:

```python
        if batch.swing_signals:
            subs_path = signals_path / "state" / "push_subscriptions.json"
            if subs_path.exists():
                subs = json.loads(subs_path.read_text())
                from pipeline.push import send_push
                for sig in batch.swing_signals:
                    payload = {
                        "title": f"{sig.mode} · {sig.action} {sig.symbol}",
                        "body": f"Entry ₹{sig.entry:.2f} · SL {sig.stop_loss:.2f} · Tgt {sig.target:.2f}",
                        "tag": sig.signal_id,
                        "data": {"url": f"https://kite.zerodha.com/chart/web/ciq/NSE/{sig.symbol}/day"},
                    }
                    failed = send_push(payload, subs)
                    if failed:
                        errors.append(f"push_failed: {len(failed)} endpoints")
```

Also `import json` at top if not present.

- [ ] **Step 2: Add VAPID env vars to workflow**

Modify `.github/workflows/signal-pipeline.yml` — extend the `env:` block of the "Run pipeline" step:

```yaml
        env:
          FYERS_APP_ID: ${{ secrets.FYERS_APP_ID }}
          FYERS_ACCESS_TOKEN: ${{ secrets.FYERS_ACCESS_TOKEN }}
          VAPID_PRIVATE_KEY_PEM: ${{ secrets.VAPID_PRIVATE_KEY }}
          VAPID_CONTACT: ${{ secrets.VAPID_CONTACT }}
          SIGNALS_REPO_PATH: /tmp/signals
```

- [ ] **Step 3: Human E2E test**

- On phone: open the deployed Streamlit URL in Chrome, install as PWA, go to Settings → tap "Enable push notifications", copy JSON to signals-repo `state/push_subscriptions.json`, commit.
- Trigger a workflow run: `gh workflow run signal-pipeline.yml`
- Wait ~1 min.
- Expected: push notification appears on phone. Tap it → opens Kite chart for that symbol.

- [ ] **Step 4: Commit**

```bash
git add pipeline/run.py .github/workflows/signal-pipeline.yml
git commit -m "feat(pipeline): dispatch web push on new signals — E2E first alert (T1.14)"
```

---

### Task 1.15: Self-heartbeat + advanced-drawer cull

**Files:**
- Create: `.github/workflows/heartbeat.yml`
- Move: `pages/2_Dashboard.py` → `pages/Advanced/2_Dashboard.py`
- Move: `pages/10_Learn.py`   → `pages/Advanced/10_Learn.py`
- Move: `pages/11_Settings.py` → `pages/Advanced/11_Settings.py` (unless T1.13 already placed it there)

**Interfaces:**
- Consumes: `SIGNALS_DEPLOY_KEY`, `VAPID_*` secrets
- Produces: separate 15-min cron that fires a "pipeline dead" push if the main pipeline's `pipeline_health.json.last_run_ts` is older than 15 min

- [ ] **Step 1: Create heartbeat workflow**

Create `.github/workflows/heartbeat.yml`:

```yaml
name: heartbeat
on:
  schedule:
    # Every 15 min during market hours + 30 min buffer
    - cron: '*/15 3-11 * * 1-5'
  workflow_dispatch: {}

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 2
    steps:
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install pywebpush
        run: pip install pywebpush py-vapid requests
      - name: Setup SSH for signals repo
        env:
          DEPLOY_KEY: ${{ secrets.SIGNALS_DEPLOY_KEY }}
        run: |
          mkdir -p ~/.ssh
          echo "$DEPLOY_KEY" > ~/.ssh/signals_key
          chmod 600 ~/.ssh/signals_key
          printf "Host github-signals\n  HostName github.com\n  IdentityFile ~/.ssh/signals_key\n  StrictHostKeyChecking no\n" >> ~/.ssh/config
      - name: Clone signals repo
        env:
          SIGNALS_REPO_URL: ${{ secrets.SIGNALS_REPO_URL }}
        run: |
          SSH_URL="${SIGNALS_REPO_URL/git@github.com/git@github-signals}"
          git clone "$SSH_URL" /tmp/signals
      - name: Check + alert
        env:
          VAPID_PRIVATE_KEY_PEM: ${{ secrets.VAPID_PRIVATE_KEY }}
          VAPID_CONTACT: ${{ secrets.VAPID_CONTACT }}
        run: |
          python - <<'PYEOF'
          import json, os, sys
          from datetime import datetime, timezone, timedelta
          from pathlib import Path
          from pywebpush import webpush

          h = json.loads(Path("/tmp/signals/state/pipeline_health.json").read_text())
          ts = h.get("last_run_ts")
          if ts is None:
              sys.exit(0)
          last = datetime.fromisoformat(ts)
          age = datetime.now(timezone.utc) - last
          if age <= timedelta(minutes=15):
              print(f"OK — last run {age.total_seconds()/60:.1f} min ago")
              sys.exit(0)
          subs_path = Path("/tmp/signals/state/push_subscriptions.json")
          if not subs_path.exists():
              print("Pipeline dead but no push subscribers")
              sys.exit(1)
          subs = json.loads(subs_path.read_text())
          payload = json.dumps({"title": "🛑 Pipeline dead", "body": f"Last run {int(age.total_seconds()/60)} min ago"})
          for s in subs:
              try:
                  webpush(subscription_info=s, data=payload,
                          vapid_private_key=os.environ["VAPID_PRIVATE_KEY_PEM"],
                          vapid_claims={"sub": os.environ["VAPID_CONTACT"]})
              except Exception as e:
                  print(f"push failed: {e}")
          PYEOF
```

- [ ] **Step 2: Cull noise pages into Advanced/ subdirectory**

```bash
mkdir -p pages/Advanced
git mv pages/2_Dashboard.py pages/Advanced/2_Dashboard.py
git mv pages/10_Learn.py    pages/Advanced/10_Learn.py
# 11_Settings.py already in Advanced/ from T1.13; if not:
[ -f pages/11_Settings.py ] && git mv pages/11_Settings.py pages/Advanced/11_Settings.py || true
```

Streamlit multi-page auto-discovers files in `pages/` but not subdirectories, so this hides them from primary nav. Add a link on Today page:

```python
# in pages/0_Today.py, at the very bottom:
st.markdown("---")
st.caption("🔧 Advanced pages (Dashboard, Learn, Settings) are hidden from main nav. Access via URL: `?page=Advanced/2_Dashboard` etc.")
```

- [ ] **Step 3: Commit everything**

```bash
git add .github/workflows/heartbeat.yml pages/
git commit -m "feat(pipeline): heartbeat workflow + move noise pages to Advanced (T1.15)"
git push
```

- [ ] **Step 4: Manual verification**

- Wait for next heartbeat cron (or `gh workflow run heartbeat.yml`)
- Confirm it logs "OK — last run X min ago" during normal operation
- Deliberately break: set the workflow schedule off, wait 20 min, run heartbeat manually — expect a push notification

## L1 Loop Exit Criteria

- [ ] Pipeline runs every 5 min during NSE hours without error for 1 full trading day
- [ ] Signals repo receives an audit-log commit per run
- [ ] Chrome PWA installed on phone; regime cockpit visible
- [ ] At least one push notification received from a live signal
- [ ] Trust badge shows healthy when pipeline is up, degraded/dead when broken (deliberately test by disabling workflow)
- [ ] `pytest tests/pipeline/ tests/components/ -v` all green

---

*Loops 2, 3, 3.5, 4 to be appended to this file in follow-up sessions after L1 exit criteria pass.*
