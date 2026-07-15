# L1 Human Setup Checklist

**Run these steps yourself before invoking subagent-driven-development on the L1 plan.** Subagents cannot log into your GitHub, sign up for Fyers, or install PWAs on your phone. Everything below is either a one-time credential setup or requires your device.

**Branch:** `feat/l1-pipeline-pwa` (already created + checked out — do not merge to main until L1 exit criteria pass).

---

## Setup Step 1 — Create private signals repo + deploy key (T1.1 in plan)

Estimated time: 10 min. Run in a terminal at the project root.

```bash
# 1. Create the private repo (do not initialize signals dir yet)
gh repo create nse-trading-lab-signals --private --add-readme \
  --description "Private signal + ledger store for nse-trading-lab pipeline"

# 2. Generate ed25519 deploy key (unique to this pipeline)
ssh-keygen -t ed25519 -C "nse-trading-lab-pipeline" -f /tmp/nse_deploy_key -N ""

# 3. Attach public key to the signals repo with WRITE access
gh repo deploy-key add /tmp/nse_deploy_key.pub \
  -R "$(gh api user --jq .login)/nse-trading-lab-signals" \
  --title "pipeline-writer" --allow-write

# 4. Store the private key + SSH URL as secrets in THIS repo
gh secret set SIGNALS_DEPLOY_KEY < /tmp/nse_deploy_key
gh secret set SIGNALS_REPO_URL \
  --body "git@github.com:$(gh api user --jq .login)/nse-trading-lab-signals.git"

# 5. Clean up local key files (safe — GitHub holds both halves now)
rm /tmp/nse_deploy_key /tmp/nse_deploy_key.pub

# 6. Bootstrap the signals repo with initial state stubs
git clone "git@github.com:$(gh api user --jq .login)/nse-trading-lab-signals.git" /tmp/signals-bootstrap
cd /tmp/signals-bootstrap
mkdir -p state signals paper_ledger equity_daily
echo '{"last_run_ts": null, "status": "not-yet-run", "errors": []}' > state/pipeline_health.json
echo '{"generated_at": null, "regime": null, "signals": []}' > state/latest.json
git add -A
git commit -m "chore: bootstrap state directories (T1.1)"
git push
cd -
rm -rf /tmp/signals-bootstrap
```

**Verification:**
```bash
gh secret list | grep -E "SIGNALS_(DEPLOY_KEY|REPO_URL)"    # both should list
gh api "repos/$(gh api user --jq .login)/nse-trading-lab-signals/contents/state" --jq '.[].name'
# expect: latest.json + pipeline_health.json
```

---

## Setup Step 2 — Free Fyers account + API credentials (T1.8 in plan)

Estimated time: 15 min. Requires PAN + Aadhaar for demat KYC.

1. Visit https://fyers.in/ and open a free demat account (no minimum balance, no monthly fee — data API is free).
2. Once account is active, visit https://myapi.fyers.in/
3. Create a new App → App type "Web" → redirect URL `https://localhost/` (works for personal use).
4. Note the **App ID** (looks like `XXXXXXXX-100`).
5. Generate an **access token** via the "Authenticate" button — completes in browser, gives you a long token string.
6. Store as secrets:

```bash
gh secret set FYERS_APP_ID --body "<paste App ID here>"
gh secret set FYERS_ACCESS_TOKEN --body "<paste access token here>"
```

**Note on token expiry:** Fyers access tokens are valid for ~24 hours. In Loop 4 we'll add a daily nag push if it expires. For Loop 1 execution, just refresh once daily via the same "Authenticate" button.

**Verification:**
```bash
gh secret list | grep FYERS_
# expect: FYERS_ACCESS_TOKEN, FYERS_APP_ID
```

---

## Setup Step 3 — Generate VAPID keys for Web Push (T1.13 in plan)

Estimated time: 5 min. Uses the `py-vapid` library.

```bash
pip install py-vapid pywebpush

python <<'PYEOF'
from py_vapid import Vapid
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import base64

v = Vapid()
v.generate_keys()
v.save_key('/tmp/vapid_private.pem')

pub_bytes = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
pub_b64 = base64.urlsafe_b64encode(pub_bytes).decode().rstrip('=')
print("VAPID_PUBLIC_KEY (base64url):")
print(pub_b64)
PYEOF

# Store as secrets
gh secret set VAPID_PRIVATE_KEY < /tmp/vapid_private.pem
gh secret set VAPID_PUBLIC_KEY --body "<paste base64url public key from above>"
gh secret set VAPID_CONTACT --body "mailto:gaurav.kumar@loglass.co.jp"

# Clean up
rm /tmp/vapid_private.pem
```

**Verification:**
```bash
gh secret list | grep VAPID_
# expect: VAPID_CONTACT, VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY
```

---

## Setup Step 4 — Deploy-time PWA static base (T1.14 in plan)

Estimated time: 2 min. Configure Streamlit host environment.

On your Streamlit Cloud (or self-hosted) instance, set the following environment variable:

```bash
PWA_STATIC_BASE=https://raw.githubusercontent.com/<your-github-username>/nse-trading-lab/main/static
```

Replace `<your-github-username>` with your actual GitHub username (e.g., `gauge123`). This tells the PWA loader where to find service-worker.js and manifest.json at deploy time.

**Verification:**
Check Streamlit deployment settings → Environment variables → confirm `PWA_STATIC_BASE` is set correctly.

---

## When done: come back and say "L1 setup done"

I'll then:
1. Verify all 6 secrets are set (via `gh secret list`)
2. Verify signals repo exists + is bootstrapped
3. Dispatch a subagent to execute T1.2 (workflow skeleton), followed by T1.3–T1.7 (pure-code pipeline modules), T1.9–T1.12 (UI components)
4. Pause before T1.13's UI subscription flow (needs your phone) and T1.14 (E2E test on your device)
5. Guide you through the T1.13/T1.14 phone-side steps
6. Dispatch T1.15 (heartbeat workflow + Advanced/ cull) as final subagent task
7. Run final whole-branch review before you merge to main

## What NOT to do while paused

- Don't merge `feat/l1-pipeline-pwa` to main (nothing shippable is on the branch yet).
- Don't delete or rename the private signals repo once created — the pipeline is hard-coded to push to it.
- Don't share the Fyers access token or VAPID private key — they can move money / send push respectively.
