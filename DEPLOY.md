# Deploying to Streamlit Community Cloud

One-time setup. Five minutes. Every push to `main` after this auto-redeploys.

## Prerequisites (one-time, per account)

- GitHub account: ✅ Gaurav1112 (you)
- Streamlit account: sign in with your GitHub at https://share.streamlit.io

That's it. No credit card, no install. Repo is public (free tier covers it).

## The five clicks

1. **Visit https://share.streamlit.io**, sign in with GitHub.
2. **Click "Create app" → "Deploy a public app from GitHub".**
3. **Fill the form:**
   - Repository: `Gaurav1112/nse-trading-lab`
   - Branch: `main`
   - Main file path: `ui.py`
   - App URL (optional): pick a custom subdomain like `nse-trading-lab` →
     gives you `https://nse-trading-lab.streamlit.app`
4. **(Optional but recommended) Click "Advanced settings" → "Secrets"**
   and paste the contents of `.streamlit/secrets.toml.example` after
   uncommenting and customizing whatever you want non-default.
5. **Click "Deploy".**

First deploy takes ~2 minutes (Cloud builds the container, installs
requirements.txt, boots Streamlit). After that, every push to `main`
auto-redeploys in ~30 seconds — no manual intervention.

## Important: what runs on Cloud vs locally

| Feature | Streamlit Cloud | Local (`./start.sh`) |
|---|---|---|
| Picks page, Analyze, Tape Monitor, Backtest, Risk Lab | ✅ | ✅ |
| Position tracking across browser refreshes | ⚠️ session-only | ✅ persistent at `~/.nse-trading-lab/` |
| Trade journal | ⚠️ session-only | ✅ persistent |
| Audit log | ⚠️ session-only | ✅ persistent |

**Why:** Streamlit Cloud's container filesystem is ephemeral — anything
written there vanishes when the container restarts (usually nightly, or
on redeploy). For ongoing trade journaling, do that locally.

**Two reasonable workflows:**

- **Cloud for analysis, local for trade entry.** Use the Cloud URL from
  your phone or anywhere to read the daily picks, then write the trade
  into your local app when you actually open the position. The daily
  tape report at `docs/daily/YYYY-MM-DD.md` is also browsable from
  GitHub directly.
- **Cloud only, with manual export.** Use the Cloud app exclusively
  and export your positions JSON to your laptop periodically.
  (Position-export download button is the natural next feature.)

## Authentication (optional, recommended)

Streamlit Cloud free tier offers private apps via Google sign-in. To
restrict access to your email only:

1. Cloud dashboard → your app → **Settings → Sharing**
2. Set "App access" to **Restricted**
3. Add `gaurav.kumar@loglass.co.jp` (and any other addresses) to the
   allowed list.

Without this, the app URL is public — anyone who knows the URL can use
it. That's *fine* for an analysis tool (no private data is rendered to
new sessions), but enabling auth gives you peace of mind.

## What auto-deploys after the one-time setup

After share.streamlit.io is set up, you literally never touch it again.
Each `git push origin main` triggers:

1. Streamlit Cloud detects the push.
2. Cloud rebuilds the container, installs `requirements.txt`, boots
   `ui.py` with `.streamlit/config.toml`.
3. Cloud also reads `.streamlit/secrets.toml` (whatever you pasted into
   the Secrets UI on share.streamlit.io) and exposes them as env vars.
4. App becomes live again within ~30 seconds.

The three GitHub Actions workflows we set up alongside Cloud:

- `ci.yml` — pytest on every push.
- `daily-tape-report.yml` — commits `docs/daily/YYYY-MM-DD.md` at
  18:00 IST every weekday.
- `weekly-refresh.yml` — regenerates walk-forward + calibrator every
  Sunday 08:30 IST.

These run inside GitHub's runners; they are independent of Streamlit
Cloud and do not consume Cloud resources.

## Verifying after deploy

Visit your Cloud URL. The home page should render with the dark theme.
The sidebar should show "Quick Watchlist" and capital figure. Click
"Today's Picks" — you should see the Tape Regime banner (HOSTILE today)
and "0 picks is the right answer" empty state.

If the cold-start fetch of yfinance fails on the first request, refresh
once — Cloud aggressively caches after the first successful pull.

## Troubleshooting

- **Page loads blank, "An error has occurred" banner.** Check Cloud logs
  via the menu in the bottom-right of your app — most likely a missing
  dependency that needs to be added to `requirements.txt`.
- **yfinance 429 / rate limit.** Cloud IPs share a small egress pool;
  yfinance occasionally throttles. The data layer caches results so a
  retry after a few minutes usually succeeds.
- **App "Zzz" sleeping.** Free tier suspends apps after a few hours of
  no traffic. First visit after sleep takes ~20s to wake.
