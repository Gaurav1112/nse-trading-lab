# Residual gaps — what is out of scope at the current configuration

This file lists every gap that exists in the app and **cannot be closed by
me writing code alone**. Each item has a clear "what would close it" so you
can decide if it's worth the cost.

Last updated: 2026-06-17

---

## 1. SEBI Streamlit Cloud authentication gate

**What's missing:** the public URL https://nse-trading-lab.streamlit.app
serves BUY/SELL verdicts to any visitor. Under SEBI Research Analysts
Regulations 2014 §3, that's plausibly unregistered investment advice.

**What closes it:** 5 clicks at share.streamlit.io, no code:
1. Settings → Sharing → "Only specific people"
2. Add your email (and any backup emails you trust)
3. Save

**Why I can't:** share.streamlit.io is gated by your Google login.

**Cost:** ₹0, 5 minutes.

---

## 2. Real intraday data (Kite Connect ticker)

**What's missing:** yfinance is an end-of-day feed. During market hours
the most recent bar is yesterday's close. We work around this with NSE's
`/api/quote-equity` for live CMP, but for true tick-level intraday data,
you need Zerodha's Kite Connect ticker subscription.

**What closes it:** Kite Connect subscription. Pricing: ~₹2,000/month for
ticker + historical OHLC + order API. Get the API key, paste into
`.streamlit/secrets.toml` on Streamlit Cloud.

**Why I can't:** I can't pay your money, and I can't sign you up under your
PAN.

**Cost:** ₹2,000/month + ~30 minutes of code to wire the Kite client in.

---

## 3. Automated order placement (Kite Connect orders API)

**What's missing:** today the app shows Zerodha order steps that you
copy-paste into Kite manually. With Kite Connect, the app could place
orders directly.

**What closes it:** same Kite Connect subscription as #2, plus an explicit
opt-in toggle in Settings ("AUTHORIZE THE APP TO PLACE REAL ORDERS"). I
strongly recommend you don't enable this until you've paper-traded
through at least one regime cycle.

**Why I can't:** Kite API key gate.

**Cost:** ₹2,000/month + ~3-4 hours of code (order placement, position
reconciliation, failure handling).

---

## 4. Options chain + futures data

**What's missing:** trading_modes.py has options/futures stubs but no
real options chain (call/put strikes, OI, IV). yfinance Indian options
data is unreliable.

**What closes it:** Kite Connect (which includes options) OR a paid feed
like Definedge, Sensibull API, or NSE's official `/api/option-chain`
endpoint (which they block bots harder than `/api/quote-equity`).

**Why I can't:** no free reliable source for Indian options.

**Cost:** Kite Connect already covers it. Sensibull starts at ~₹1500/month
for API access.

---

## 5. Historical NSE Nifty 50 composition (survivorship bias)

**What's missing:** our `NIFTY50_SYMBOLS` is the **current** roster
replayed back to 2023. Names that were in the Nifty 50 in 2023 but got
booted (often poor performers) are invisible — biases published
expectancy upward by an unknown but non-zero amount. Lopez de Prado
calls this "the most overlooked bias in retail quant."

**What closes it:** NSE Archives publishes semi-annual rebalance lists.
Scrape them, build a date-indexed composition map, modify
`picker_replay.py` to use the per-date roster instead of the global one.
Then re-run walk-forward — expect numbers to drop.

**Why I haven't:** ~1 weekend of careful work, and the answer will likely
be "the held-out 2026 number was already honest; the in-sample 2023-2025
numbers were even more overstated than we admitted." Not high priority
unless you want to defend the engine to a quant audience.

**Cost:** 1-2 weekends of work. No money.

---

## 6. LightGBM-based calibration with proper held-out CV

**What's missing:** Phase 3 v1 attempted isotonic; we documented that
LightGBM at 390 trades will overfit. Doing it "properly" requires:
- Walk-forward CV with embargo (we have purged_kfold_indices() now)
- Triple-barrier labeling (we have block bootstrap; triple-barrier is
  more involved — relabel each trade as which barrier hit first)
- More features than score + win_prob (regime, sector, VIX, day-of-week)
- More data — really wants n ≥ 2,000 trades

**What closes it:** either collecting another year of walk-forward trades
(~390 more, so ~12 months), or accepting overfit risk on the current
data scale and reporting honest held-out Brier.

**Why I haven't:** at this data scale, the audit panel was unanimous
that LightGBM will lose to isotonic on held-out Brier. Building it just
to show the loss isn't useful.

**Cost:** 1 weekend.

---

## 7. Mobile-native trading app

**What's missing:** Streamlit on a phone is *functional* after the D1
mobile CSS work (column stacking, 44px buttons, font scaling), but it's
not a native app. Things like push notifications on T1 hit, biometric
auth, offline access — none of these.

**What closes it:** rewrite the front-end as a React Native app or a PWA
shell around the Streamlit backend. Either is a multi-week project.

**Why I haven't:** scope. Streamlit Cloud + mobile-friendly CSS is the
practical sweet spot for a personal trading lab.

**Cost:** 3-4 weeks of focused work, or hiring a contractor.

---

## 8. Multi-user / SaaS scaling

**What's missing:** the app is single-user. Multiple users hitting the
URL get the same session-state semantics that can leak preferences and
position state in subtle ways.

**What closes it:** add real auth (Supabase / Firebase / Auth0), per-user
data isolation, and probably a real database instead of JSON files.

**Why I haven't:** you're the only intended user. Streamlit Cloud auth
restricting to your email is sufficient at this scale.

**Cost:** 2-3 weeks.

---

## 9. AI chat assistant ("explain this pick")

**What's missing:** several competitor products have an in-app chat that
explains "why is this stock scored at 82" in natural language using an
LLM.

**What closes it:** Anthropic Claude API or OpenAI GPT API + the
existing scorer reasons + a small system prompt. Cost is per-query
(~₹0.10 per question with Haiku-tier models).

**Why I haven't:** marginal value over the existing "Why this stock"
expander. Could ship as Phase X if you want it.

**Cost:** ~₹1000/month at modest usage, ~1 day of code.

---

## Action summary

| # | Item | Cost (₹/month) | Action |
|---|------|----------------|--------|
| 1 | Streamlit Cloud auth | ₹0 | **YOU** — 5 minutes on share.streamlit.io |
| 2 | Kite Connect ticker | ₹2,000 | YOU — sign up, paste API key into secrets |
| 3 | Automated orders | (included in #2) | YOU — explicit opt-in toggle |
| 4 | Options/futures data | (included in #2) | YOU — same Kite key |
| 5 | Survivorship bias fix | ₹0 | ME — 1 weekend if requested |
| 6 | LightGBM with proper CV | ₹0 | ME — wait for more data first |
| 7 | Mobile-native app | (none monthly) | OUTSIDE SCOPE |
| 8 | Multi-user SaaS | (none monthly) | OUTSIDE SCOPE |
| 9 | AI chat assistant | ₹1,000 | ME — 1 day if requested |

**The single highest-impact action you can take right now is #1.** It
costs nothing and converts a real SEBI compliance risk into compliance.
