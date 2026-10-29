# NSE Trading Lab

A systematic stock analysis, screening & backtesting platform for Indian stock markets (NSE/BSE).

Built to help you **make data-driven trading decisions instead of gut-feel bets**.

## Features

- **Web Dashboard** — Full UI with stock analyzer, screener, backtester, position tracker
- **GO/WAIT/AVOID Verdicts** — 6-dimensional scoring engine rates every stock
- **Swing Screener** — 6 scan types: breakout, reversal, squeeze, volume surge, supertrend flip, trend continuation
- **11 Built-in Strategies** — All backtested with look-ahead bias protection (Zerodha cost model + STT/IPFT)
- **Zerodha Cost Model** — Exact STT, stamp duty, GST, slippage
- **MTF Position Tracker** — Interest burn projection, breakeven drift, exit scenarios
- **Position Sizing** — 2% risk rule with exact share counts

## Quick Start

```bash
# 1. Setup
unzip nse-trading-lab.zip
cd nse-trading-lab
python -m venv venv
source venv/bin/activate   # Linux/Mac
# venv\Scripts\activate    # Windows
pip install -r requirements.txt

# 2. Launch the Web UI
streamlit run ui.py
# Opens http://localhost:8501 in your browser

# Or use the launch script
chmod +x start.sh && ./start.sh
```

## Alternative: Command Line

```bash
# Analyze any stock
python app.py analyze RELIANCE --full

# Run screener
python app.py screen

# Backtest strategies
python app.py backtest TCS --stop-loss 0.07

# Compare stocks
python app.py batch RELIANCE TCS INFY SBIN

# Demo mode (no internet)
python app.py demo
```

## Trade Desk

The Trade Desk turns a stock you are looking at into one sized decision: **TAKE**, **WAIT** or **SKIP**,
for **NSE** (₹, IST) and **US** (US$, ET) stocks. Each market keeps its own settings, journal rows and
money formatting; amounts are never mixed across markets.

Open it with `streamlit run ui.py` (or `./start.sh`) and pick the **Desk** page (tabs: Choose a Trade, Today's Plan,
Check a Trade, My Positions, Intraday, Scoreboard), or from the terminal:

```bash
python3.13 -m nse_backtest.desk check RELIANCE            # one stock, swing
python3.13 -m nse_backtest.desk check AAPL --market US --mode intraday
python3.13 -m nse_backtest.desk check RELIANCE --qty 20     # the money for your own quantity
python3.13 -m nse_backtest.desk check RELIANCE --choose     # take a WAIT/SKIP anyway (logged as your override)
python3.13 -m nse_backtest.desk check RELIANCE --desk-entry # swing at the plan's own entry, not the last close
python3.13 -m nse_backtest.desk check INFY --mode intraday --side short   # ORB breakdown short (NSE, HOSTILE days)
python3.13 -m nse_backtest.desk shortcheck                 # may shorts be a TAKE today? (pooled history, cached per day)
python3.13 -m nse_backtest.desk plan                      # today's TAKE / WATCH lists
python3.13 -m nse_backtest.desk plan --all                # every candidate, ranked, with the money
python3.13 -m nse_backtest.desk positions                 # HOLD / TIGHTEN_STOP / EXIT
python3.13 -m nse_backtest.desk scoreboard                # paper record of logged TAKEs (all markets)
python3.13 -m nse_backtest.desk scoreboard --market US --json
```

- **TAKE**: no check blocks and the current price (live quote, else the last close with a WARN) is
  within about 1% of the planned entry (intraday: at or below the stop-limit's limit price). You get a
  ticket to type into your broker yourself.
- **WAIT**: nothing blocks, but the price is below the entry (buy only above the trigger) or has run more
  than 1% past it (intraday: above the stop-limit's limit, so the order would not fill). The ticket is
  shown as "IF TRIGGERED".
- **SKIP**: at least one check BLOCKs (named in the headline), or there is no usable current price.

### Choose a Trade

The first tab is a "your call" ranking. It lists every stock the desk has checked today, with levels and money, not
only the TAKEs: the Today's Plan TAKE, WATCH and "also checked" stocks, and the Intraday-tab scan (build them on
their own tabs, or press the load button on this tab). On a day when the desk says "TAKE: none", the table still
shows each stock's entry, stop, targets, quantity, max loss, profit at T1, expected value and win odds, so you can
decide for yourself. Order: TAKE, then WAIT, then SKIPs blocked only by the market regime or the odds (advice),
then SKIPs blocked by data or limits; within a group, by Monte Carlo expectancy, best first. Extra columns are
"Checked", "To entry %", "Exp R" and "Expected value". `plan --all` prints the same ranked table, with a
`check SYM --choose --desk-entry` hint per row.

- **Quick verdicts need a full check.** The plan prices its first pass on the last close with no live quote. Those
  rows are marked "(quick check)"; their card says "Quick check — run a full check before choosing", has a
  **Full check** button, and shows no ticket and no override box until the full check has run. A quick verdict
  has an empty `ticket` in `plan --json`. A TAKE demoted by the full check is not replaced by the next quick one.
- "Check it" and Check a Trade use the plan's own entry (swing: breakout above the 20-day high), not the last
  close. On the CLI that is `check SYM --desk-entry`; plain `check` still uses the last close unless you pass
  `--entry`.

Pick one to open its trade card:

- **Money** (at the desk's size, or at a quantity you type in): max loss, profit at T1, profit at T2,
  reward:risk, expected value, and the share of capital deployed and at risk.
  - Max loss = quantity × (worst entry − stop) + round-trip costs with the exit at the stop. The worst entry is
    the entry for swing and the stop-limit's limit for intraday (an intraday entry can fill anywhere up to its
    limit), and the desk sizes on that same figure. Intraday cards also show the max loss if filled at the
    trigger. The verdict headline's "loss at stop" is the same number. A gap through the stop can lose more,
    and the card says so.
  - Profit at T1 (T2) = quantity × (target − entry) − round-trip costs with the exit at that target.
  - Expected value = Monte Carlo expectancy in R × quantity × (entry − stop). It is blank when there are no odds.
    Intraday, it and the account projection assume a fill at the trigger; the card says how much less a fill
    at the limit can make (or, when the ticket is a plain limit because the price is already above the trigger,
    about quantity × (current price − entry) less).
  - If the desk sized 0 shares (for example the stop is too wide for your risk limit) there is no total to
    show, so the card gives the per-share loss and profit at T1 and T2 and asks for a quantity.
- **Breaches**: a quantity you type in that goes past your risk-per-trade, position-size or daily-loss limit is
  listed in red. Risk per trade compares the max loss (costs included) with your limit, the sizer's own rule.
  The daily-loss line compares this trade alone with the limit; losses already taken today are checked by the
  daily-loss check. The desk still shows the numbers; the decision is yours.
- **Advice versus blocks.** Each reason on the card sits in one group:
  - *Market* (the regime check): an amber **Market says sit out** banner, with what would flip it. This is advice.
  - *Odds* (Monte Carlo, setup history, robustness, earnings; for shorts the edge gate, the short Monte Carlo and
    relative volume): an amber **Advice** box. Also advice.
  - *Data* (freshness, live price, gap) and *limits* (size, risk governor, daily loss, PDT, ban list, stop-out
    streak, liquidity, entry window, reward:risk): red **Real problems**. Data and limits warnings that do not
    block are amber "Check before entering".
  - *Safety* (a short that is not an F&O stock, is in the F&O ban, or whose lists cannot be verified): red "This
    short can't be taken". No ticket, no override box, and `check --choose` refuses.
- **The override gate**: for a WAIT or SKIP the ticket stays hidden until you tick "I choose this anyway". The
  ticket is then headed "YOUR OVERRIDE — the desk said SKIP" and the choice is logged once per verdict (one
  row, with the quantity at the first tick; changing the quantity afterwards adds no rows). The CLI does the
  same with `check --choose` (which always runs a fresh check). The override row is stamped with the time you
  chose (the verdict's own time is kept as `verdict_created_at`), and the scoreboard replays it from then, so a
  move that ran before your decision is never credited to it. A WAIT also shows the desk's own conditional
  ticket ("IF TRIGGERED") without needing an override.
  - **Overriding the market regime** is allowed and costs a stated amount. On NSE the header reads "YOUR
    OVERRIDE — against the market regime (HOSTILE): the desk's held-out longs here averaged -1.61% per trade, a
    loss of about ₹x on this position" (the percentage times the position's notional). The held-out figure is
    measured for NSE HOSTILE only; US and other regimes get the warning without a number.
  - **Re-check on broken data.** If any data check BLOCKs, there is no override box and no ticket, only a
    **Re-check** button ("Data is broken (...) — re-check before choosing"). `check --choose` prints "--choose
    refused: data is broken" and exits 1. Broken data is not an opinion to overrule.
  - **F&O ban.** For a long (swing or intraday) the ban is a WARN: derivatives are banned, cash is allowed. For
    an intraday short it is a safety BLOCK, and so is a ban list that cannot be verified or is stale.
- **Age**: the candidates table has a "Built" column and every card says "built HH:MM (n min ago)". An intraday
  verdict older than 15 minutes, or shown past the entry cutoff, has no ticket and no override box: it shows a
  Re-check button that runs the full check again and replaces the card's verdict. Swing tickets stay, with the
  age shown.
- **Outcome money**: the Monte Carlo replays turned into money (5th to 95th percentile, median, P(target),
  P(stop), P(time exit)) and a histogram marking the stop and targets.
- **Price chart**: recent candles with entry, stop and targets, plus a band built from the stock's own past
  moves over the trade's hold (intraday: the bars left until flat-by).
- **Account projection**: the same trade repeated for 10, 20 or 50 trades (20 by default), each
  drawn from the Monte Carlo replays (else the setup history), at a fixed money risk and no compounding:
  the 5th, 50th and 95th percentile of the account and the share of runs with a 10% drawdown or a loss.
- Every projection is a range of the stock's past behaviour, **not a forecast**.

- **Holding up (not a trade).** Below the table, up to 10 stocks that are above their own 50-day EMA and beating
  the index over 60 days, ranked by that lead in index points. It is a watchlist for when the regime turns: no
  money, no ticket, no action. If the index and the stocks' last bar dates differ, stocks on a different day are
  left out (or the whole list is withheld) and the page says why. `plan` prints it as HOLDING UP; in `plan --json`
  it is `holding_up: {"note", "stocks": [{"symbol", "vs_index_pts"}], "unavailable"}`.

Check a Trade from the terminal prints the same Money block:
`python3.13 -m nse_backtest.desk check RELIANCE --qty 20` uses your quantity instead of the desk's size
(breaches are listed), and `--choose` prints and logs the override ticket for a WAIT or SKIP.

### Intraday scan and ORB breakdown shorts

The Intraday tab scans the **whole universe** (not the first 15), with a progress bar, behind its button. Every
row (TAKE, WAIT or SKIP) has an Open card button that shows the same trade card and override rules as above.

**ORB breakdown shorts** are for **HOSTILE NSE days only**, on **F&O stocks only**. The reasoning: in a falling
market the evidence favours cash, and the one intraday idea worth testing is a short through the first
15-minute bar's low on a liquid stock. A short is cash-segment MIS; it is not a futures or options trade.
- Plan: opening range = the first 15-minute bar. Entry trigger = its low, stop = its midpoint, T1 = entry - 1.5R,
  T2 = entry - 3R. The sell limit is the smaller of 0.25R and 0.5% of the entry below the trigger. Size comes from
  the rupee risk at the worst fill (stop minus the limit).
- Checks beyond the long list: `short_regime` (not HOSTILE or not NSE = BLOCK, advice), `short_fno` (safety
  BLOCK), `ban_list` (BLOCK for shorts), `relative_volume` (WARN unless the opening bar's volume is at least 1.5×
  its 20-session median), `short_edge` and `short_monte_carlo`. Setup history and robustness are not run for
  shorts.
- **The edge gate.** A short can be a desk TAKE only if two things hold. First, the pooled ORB-short history over
  today's eligible universe (F&O list, HOSTILE days only, about 60 days of 15-minute bars) has **at least 200
  trades and a positive expectancy at 2× slippage**. Second, the stock's own simulated short odds are positive
  (`short_monte_carlo`). Otherwise the short is at most WAIT with "edge not proven on the desk's own history yet
  (n trades, x R)", and you can still take it as your own call. Per-stock history is too short to prove an edge
  alone, hence the pool. Run `python3.13 -m nse_backtest.desk shortcheck` (or the button on the Intraday tab)
  **daily**: the figure is cached for the day and a check never recomputes it. Until you run it, every short
  reads "edge check unavailable". A short with a user stop or target is not the geometry the pool proves, so its
  `short_edge` check warns instead of passing.
- **Tickets** cover both price branches. At or below the trigger: `SELL n SYM MIS LIMIT {limit}`. Above it:
  `SELL n SYM MIS SL trigger {entry} limit {limit}`. Then `SL-M BUY n SYM trigger {stop}`, `BUY n SYM LIMIT {T1}`,
  "Cancel the entry if not filled by ..." and "Square off yourself by 15:10 IST (broker auto square-off charges
  ₹59+)". The desk places nothing.
- **Square off yourself by 15:10.** A retail cash-segment short has to be bought back the same day: SEBI's
  short-selling framework (circular of 5 Jan 2024) bars naked short selling, requires delivery at settlement and
  lets retail traders square off intraday. A position you cannot cover (for example a stock locked at its upper
  circuit) becomes a short delivery, settled by an exchange auction on T+1 at a price you do not control. If the
  auction finds no seller, the close-out price is the higher of 20% above the auction-day settlement price and the
  highest price since the trade (Zerodha, "Short delivery without fresh sellers", below). F&O stocks have dynamic price bands, which
  cuts that risk, so the desk allows F&O stocks only. If the broker squares you off, Zerodha charges about ₹50
  plus GST per order. Sources:
  - SEBI short-selling framework: https://www.sebi.gov.in/legal/circulars/jan-2024/framework-for-short-selling_80448.html
  - Short delivery and auctions: https://support.zerodha.com/category/trading-and-markets/trading-faqs/general/articles/what-is-short-delivery-and-what-are-its-consequences
  - Auction without fresh sellers: https://support.zerodha.com/category/trading-and-markets/trading-faqs/general/articles/short-delivery-without-fresh-sellers-auction-market
  - Dynamic price bands for F&O stocks: https://www.sebi.gov.in/sebi_data/attachdocs/feb-2019/1549883290545.pdf
  - Auto square-off charge: https://support.zerodha.com/category/trading-and-markets/trading-faqs/market-sessions/articles/intraday-auto-square-off-timings
  - Costs and MIS cut-off: https://zerodha.com/charges/
  - SEBI FY23 study (71% of individual intraday traders lost money): https://www.sebi.gov.in/sebi_data/attachdocs/jul-2024/1721818619980.pdf
- **Costs.** Intraday round trip on a ₹1,00,000 position is roughly 0.08% before slippage (brokerage, STT on the
  sell, exchange and SEBI fees, GST, stamp duty on the buy). This is an estimate from the published schedule.
- **Survivorship.** The pooled history uses today's F&O list and does not replay past bans or past membership, so
  it is flattered. The shortcheck output says "universe = today's F&O list, past bans not replayed".
- Shorts are NSE only: a US short returns BLOCK ("Shorts are NSE only") and `check --side short` refuses swing and
  US.
- The Scoreboard replays shorts with every rule mirrored and shows them as "intraday short", never pooled with
  longs (see The Scoreboard).

### Tickets

Swing: `BUY n SYMBOL CNC LIMIT {entry}` with a GTT OCO for the stop and T1 (US: a LIMIT with a GTC bracket).
Intraday entries are **stop-limit orders**, so a spike through the entry cannot fill you far above your plan:

- NSE: `BUY n SYMBOL MIS SL trigger {entry} limit {entry_limit}`; US: `BUY n SYMBOL STOP-LIMIT stop {entry}
  limit {entry_limit}`.
- When the price is already at or above the trigger (a broker rejects a stop buy whose trigger is below the last
  traded price), the entry is a plain limit at the same limit price instead: NSE `BUY n SYMBOL MIS LIMIT
  {entry_limit}`, US `BUY n SYMBOL LIMIT {entry_limit} — bracket …, DAY`. The fill rule below covers both.
- The limit is the entry plus the smaller of 0.25R and 0.5% of the entry, rounded up to the tick
  (R = entry − stop).
- A line says "Cancel the entry if not filled by HH:MM": the end of the last entry bar (the market's last entry
  time + 15 minutes, as the odds and the scoreboard count it). On an early-close day both that time and the
  square-off / flat-by time are pulled in to 15 minutes before the close. The stop and target orders and the
  flat-by time follow.
- The Time Machine odds, the Scoreboard and the live check all use the same fill rule and entry window
  (`nse_backtest/desk/fills.py`). A WAIT shows the desk's own ticket as "IF TRIGGERED".

### Research panel

Under each card, "Load news & earnings" fetches context. It is **display only and never a vote**: it cannot
change a verdict, a size or a check.

- **US**: recent headlines and 8-K filings from the vendored `trader-news` skill and the latest earnings
  change from `trader-analyst`. Both query SEC EDGAR, which needs a contact string in the environment before you
  start the app or the CLI: `export SEC_USER_AGENT="Your Name you@example.com"` (use your own name and email).
  Without it the SEC part is skipped and the panel says so.
- **NSE**: the next earnings date and recent dividends and splits, from data the desk has already loaded. There
  is no news feed for NSE. If dividends and splits cannot be loaded the earnings date still shows, with a note.
- A failed fetch shows "Research unavailable" with the reason, and pressing the button retries. Results are
  cached for 30 minutes.

### The 17 checks

This is the long list. An intraday short adds `short_regime`, `short_fno`, `relative_volume`, `short_edge` and
`short_monte_carlo`, and runs `ban_list` as a BLOCK; it skips `robustness`. The F&O ban is a WARN for longs.

Missing or failed data is never a PASS: it becomes a WARN or a BLOCK.

| # | Check | BLOCK when | WARN when |
|---|---|---|---|
| 1 | `regime` | swing and the index regime is HOSTILE or unknown | swing MIXED; intraday HOSTILE or unknown |
| 2 | `reward_risk` | reward:risk to T1 is below the minimum (default 1:1.5) | — |
| 3 | `size` | sizing gives less than one share | — |
| 4 | `risk_governor` | the portfolio governor says no new trades | the governor could not be evaluated |
| 5 | `daily_loss` | today's realised loss reached the daily limit, or no limit is set | the journal (or today's P&L) is unreadable |
| 6 | `ban_list` | an intraday short: in the F&O ban, or the list is unverified or stale | a long: in the F&O ban (derivatives only; cash allowed), or the list could not be verified or is stale |
| 7 | `monte_carlo` | no odds could be computed, or simulated expectancy is below 0R | expectancy is under +0.10R |
| 8 | `freshness` | the last daily bar misses one or more completed trading sessions (3 calendar days in a year with no holiday calendar), or the last 15-min bar is older than 30 min | the last bar is in the future, today's bar is unfinished, or the year has no holiday calendar |
| 9 | `live_price` | the live price is at or below the stop | no live quote (last close used), or the quote is over 20 min old in session |
| 10 | `entry_window` | intraday outside the entry window, on a weekend or exchange holiday, or on an early-close day | the year has no holiday calendar |
| 11 | `liquidity` | 20-day median turnover below the market minimum | turnover unknown |
| 12 | `earnings` | — | earnings inside the swing hold window, today (intraday), or unknown |
| 13 | `setup_history` | intraday: under 20 past sessions, or under +0.2R, or no history | swing: under 20 samples, or negative expectancy |
| 14 | `stoploss_streak` | — | 3 losses in a row (size is halved), or the journal is unreadable |
| 15 | `gap` | — | today's gap is over 2%, or unknown (no live day-open) |
| 16 | `robustness` | — | over 25% of candle-noise replays lose, or the test was not run |
| 17 | `pdt` | US intraday with the PDT guard on: 3+ day trades in the last 5 weekdays | the day-trade count is unknown |

Besides the BLOCK conditions in the table, the desk also fails closed (BLOCK) when the last bar's time cannot
be read, when the clock has no timezone, when the Monte Carlo expectancy is not a finite number, or when the
live price is not a valid number.

### The Time Machine

The odds come from the stock's own bars, not from a model. A block-bootstrap Monte Carlo replays the exact
entry, stop, target and hold thousands of times (fixed seed, stop wins a bar that touches both), giving
P(target), P(stop) and expectancy in R after costs. A setup history replays every past time the same setup
fired on that stock. Robustness tests shuffle the trade order (drawdown, losing streaks) and jitter the
candles to see whether the edge survives noise. For intraday setups the history and the simulation fill the
entry with the same stop-limit rule as the ticket, inside the same entry window.

### The Scoreboard

The Scoreboard checks whether the desk's own TAKEs made money, and whether your overrides of the desk
(a WAIT or SKIP you chose anyway) do better or worse. It reads every TAKE and OVERRIDE in
`desk_audit.jsonl` (TAKEs are written by Check a Trade, in the page or the CLI, by Today's Plan and by
the Intraday-tab scan) and replays each one against the prices that followed, as if the ticket had been
typed in exactly. TAKEs are scored as **Desk picks** and overrides as **Your overrides**: two separate
records, never pooled. It is a paper record, **not your real P&L**: it knows nothing about the orders you
actually placed. It is read-only and places nothing.

A **third record, "Desk sat out (market regime)"**, measures cash as a position. When the regime check is the
only BLOCK on a full-checked swing long that the desk would otherwise have TAKEn, the plan logs it as `SAT_OUT`
(once per symbol per session, for the stocks the plan re-checks with a live quote) and the scoreboard replays it
like a TAKE. Its stats show what sitting out saved or cost. It is never pooled with Desk picks or Your overrides.
Quick (first-pass) verdicts and an unknown regime are never logged as sat-outs.
Covers TAKEs from Check a Trade, Today's Plan and the Intraday-tab scan; your overrides are scored as a separate record.

Open the **Scoreboard** tab and press "Load scoreboard", or run
`python3.13 -m nse_backtest.desk scoreboard [--market NSE|US|ALL] [--json]` (default ALL).

- One TAKE per stock, mode and session is counted (the first one logged); likewise one override.
- Fills and exits are conservative. Swing: a one-session LIMIT at the entry, filled if the day's low
  reaches it; on the fill day only the stop can trigger; then up to the planned hold (15 bars by
  default) and a time exit at the last close. A swing TAKE logged while its fill session was open is
  filled from the 15-minute bars after the verdict (the day's earlier low happened before the order; the bar
  containing the verdict follows the ambiguous-fill rule below).
  Intraday: a BUY stop-limit, as the ticket prints it (trigger at the entry, limit = entry + the smaller
  of 0.25R and 0.5%, rounded up to the tick): 15-minute bars after the verdict; the first bar whose high
  reaches the trigger fills at its open or the trigger, whichever is higher, when it opens at or below the
  limit; a bar opening above the limit leaves a resting limit, filled at the limit when price trades back
  to it. Only bars inside the entry window count (the ticket says to cancel the entry if not filled by
  then); a later fill is `not_filled`. On the fill bar a stop touch exits at the stop. Flat by the
  session's flat-by time. The Time Machine's intraday history uses the same rule. When a bar touches
  both stop and target, the stop wins.
- A replay starts at the later of the verdict's time and its quote's time (the ticket can't be typed
  before the quote it was based on). The 15-minute bar that contains that start counts, for swing and intraday, but a fill that only that
  bar made possible is ambiguous: the touch may have come before the verdict. Such a fill is kept only if it
  loses (or exits at 0R or below), and its target never counts. If it would win, the order is replayed from the
  later bars, and a later fill counts only if its R is no better than the containing-bar fill's: a counted
  result is never better than either possible history. Otherwise it is `not_filled` ("fill only possible on the
  bar the TAKE was logged in — counted only if it loses"). A missing containing bar makes the trade
  `data_unavailable`. A stop-limit row with no logged price whose containing bar is inside the entry window and
  traded wholly between the trigger and the limit is a certain fill there, at the limit; one whose containing bar
  only straddles the trigger fills at the worst consistent price, the lower of that bar's high and the limit.
- Intraday rows logged at or above the trigger printed a LIMIT ticket. Priced up to the limit (desk TAKE or
  override), it fills at the logged price only when that price is trusted (below). Priced above the limit, it is
  a resting limit at the limit price. A LIMIT row logged outside the entry window, or a stop-limit row logged
  after the cancel time, is `not_filled`.
- The logged price is trusted only when it was a fresh live quote (the audit row's `quote_live`: a quote from the
  same session, at most 5 minutes old when the check used it; without a quote the desk logs the plan's last close)
  and the 15-minute bar containing the quote's own time (`quote_as_of`) traded at it (between its low and 0.5%
  above its high). Rows without a quote time are untrusted.
  An untrusted price is ignored: the trade is scored as if no price had been logged (swing and stop-limit: the
  containing-bar rules above; a LIMIT ticket: a resting limit), though which ticket was printed still follows it.
  A live price outside that bar's range contradicts the bars and makes the trade `data_unavailable`.
- One small optimism is accepted: a trusted live price (desk TAKE or override, up to the limit) fills at that
  price. A real order typed in a little later can fill worse, up to the limit (about 0.25R), and if the price
  runs above the limit while the ticket is being typed it may not fill at all (about 1% of trusted rows in a
  simulation).
- ORB shorts (NSE intraday, the SELL tickets) are replayed by the exact mirror of every rule above: a sell
  stop-limit fills between the limit and the trigger (never below the limit), a gap through the stop exits at the
  open or the stop, whichever is higher, a lost short never shows a gain, a won short exits at or below the target,
  and the containing-bar, worst-fill, gap, window and trusted-price rules all apply mirrored (a live sell price is
  trusted between 0.5% below its bar's low and its high). The ticket says to square off by 15:10, inside the
  15:00 bar: a stop in that bar counts, its target never does, and with no stop the time exit is at that bar's
  high, the worst buy-back price it allows. This is stricter than the Time Machine's short history (time exit at
  the 14:45 bar's close). Short stats are shown as "intraday short", never pooled with longs. A short row with a
  safety block (not an F&O stock, in the F&O ban, either list unverified) could never have been traded and is
  listed as a rejected line, never scored.
- Swing trades are replayed on unadjusted daily bars. A missing trading session in the bars used, or
  prices re-scaled since the ticket (a split or bonus: the logged last close no longer matches), makes
  the trade `data_unavailable` instead of a guess.
- Realised R = (exit − fill − cost per share) ÷ (entry − stop). A win is realised R above 0.
- Each trade gets a status: `won`, `lost`, `time` (time exit), `not_filled`, `open` (still running;
  shown marked to the last close, not counted) or `data_unavailable` (bars could not be loaded, have
  gaps, or are missing). Nothing is dropped silently; malformed audit lines are listed by line number.
- Per record, market, mode and side it shows trades, win %, mean realised R against the mean predicted Monte Carlo
  and history R, a 95% bootstrap interval of the mean realised R, max drawdown in R and a cumulative
  R chart.

The verdict, per record, market, mode and side, is the first of these that applies:

| Verdict | When |
|---|---|
| Not enough trades yet (n/30) | fewer than 30 resolved trades (won, lost or time) |
| Losing — do not trade this with real money | the whole 95% interval is below 0R |
| Inconclusive (trades could not be replayed) | more than 10% of resolved + unavailable trades are `data_unavailable` |
| Inconclusive (odds missing) | under 80% of the resolved trades carry a Monte Carlo prediction |
| Worse than the desk's odds — do not size up | the whole interval is below the mean predicted R |
| Matching the odds — real money at small size (≤0.5% risk) | the interval is above 0R and the mean realised R is within 0.15R of the prediction |
| Inconclusive — keep paper-trading | anything else |

Example (fictional): `NSE swing: Not enough trades yet (12/30) — keep paper-trading`.

Final results (won, lost, time, not_filled) are cached in `desk_scoreboard.json` in the data directory
(`~/.nse-trading-lab/`, or `NSE_LAB_DATA_DIR`), so they are not fetched again; open and unavailable
trades are recomputed each time. yfinance only serves 15-minute bars for about the last 60 days, so an
intraday TAKE older than that can only be shown from the cache. **Deleting `desk_scoreboard.json` loses
those intraday results for good** (they come back as `data_unavailable`). A corrupt cache, or one from
another scoreboard version, is renamed to a backup file, not overwritten.

### Holiday calendars

`nse_backtest/desk/calendars.py` holds exchange holidays and early closes:

- NSE: 2026. NSE 2027 is not published yet.
- US (NYSE): 2026 and 2027, including the 1:00 pm ET early closes.

The desk uses them for daily-data freshness (counted in trading sessions, so a long weekend is not
stale), regime staleness, the intraday entry window (BLOCK on a holiday: "Market closed today: ...";
BLOCK on an early-close day: no new intraday entries), the live-quote "market closed today" note,
partial-bar handling, and the Scoreboard's fill sessions. In a year without a calendar the desk assumes
every weekday trades, falls back to a 3-calendar-day freshness rule, and WARNs "holiday calendar not
available for {year}". NSE Muhurat trading is not supported: that Sunday is treated as a weekend and a
Muhurat bar from yfinance is dropped.

**Yearly maintenance:** when NSE publishes the next year's trading holiday circular, add the dates to `HOLIDAYS["NSE"]`, add the year to `COVERED_YEARS["NSE"]`, and record the
circular's URL in the module docstring next to the existing sources. Do the same for NYSE from
nyse.com/markets/hours-calendars (holidays and `EARLY_CLOSES`). Use the trading-holiday table, not the
clearing-holiday table.

### What the desk never does

- **Nothing is ever placed.** There is no order API anywhere in this repo; you type every order yourself.
- `.mcp.json` points Claude sessions at the hosted Kite MCP for **read-only** use (holdings, quotes).
  The desk does not use it to trade.
- `.claude/skills/trader-news` and `trader-analyst` are vendored research skills (MIT, audited, see
  `.claude/skills/VENDORED.md`). They are never inputs to a desk verdict.

Example (fictional): a TAKE might read `TAKE ABC — BUY 40 @ ₹1,000.00, loss at stop ≈ ₹980.00 (a gap
can lose more)`: the loss at the stop includes costs (intraday: at a fill up to the order's limit), and a gap
through the stop can lose more.

> **Disclaimer:** the Trade Desk is a paper-trading and education tool, not financial advice. Its odds
> are a reality check, not a forecast. Paper-trade until your record matches the odds.

### Tests

The full suite is the gate, pipeline tests included (the Fyers SDK is imported only when a Fyers
client is created, so it is not needed to run them):

```bash
python3.13 -m pytest -o addopts=""
```

Dependencies are pinned to yfinance 1.x and pandas 3.x in `requirements.txt`; install from it in a fresh
virtual environment and run the same command there before relying on a change.

**Known accepted risk:** `requests` stays at 2.31.0, because every fyers-apiv3 release (3.1.7 to 3.2.0) pins
`requests==2.31.0`. That version has CVE-2024-35195 and CVE-2024-47081, fixed in 2.32.4. The heartbeat workflow,
which does not install fyers, uses `requests>=2.32.4`. Revisit when fyers-apiv3 relaxes its pin.

## Project Structure

```
nse-trading-lab/
├── app.py                     # MAIN ENTRY POINT — one command for everything
├── nse_backtest/              # Core framework
│   ├── __init__.py
│   ├── data.py                # NSE/BSE data fetching (yfinance)
│   ├── strategies.py          # 11 built-in strategies
│   ├── engine.py              # Backtesting engine with Zerodha costs
│   ├── analytics.py           # Metrics, reports, charts
│   ├── scorer.py              # GO/NO-GO scoring engine (6 dimensions)
│   ├── screener.py            # Daily swing screener (6 scan types)
│   └── sample_data.py         # Synthetic data for testing
├── custom_strategies/         # Your own strategies go here
│   └── example_strategy.py
├── notebooks/                 # Jupyter notebooks for exploration
│   └── 01_getting_started.ipynb
├── output/                    # Generated charts, reports, CSVs
├── run_backtest.py            # Standalone backtest runner
├── demo_backtest.py           # Demo with sample data
├── requirements.txt
├── .gitignore
└── README.md
```

## Commands

| Command | What it does |
|---------|-------------|
| `python app.py analyze SYMBOL` | Full technical analysis + GO/NO-GO verdict |
| `python app.py analyze SYMBOL --full` | Above + backtests all 11 strategies |
| `python app.py screen` | Scan Nifty 50 for swing setups |
| `python app.py screen --symbols X,Y,Z` | Scan custom watchlist |
| `python app.py backtest SYMBOL` | Backtest all strategies with charts |
| `python app.py batch SYM1 SYM2 SYM3` | Rank multiple stocks side by side |
| `python app.py demo` | Run everything on sample data (no internet) |

## How The Scoring Works

The analyzer scores each stock on 6 dimensions (0-100 each):

| Dimension | Weight | What it measures |
|-----------|--------|-----------------|
| Trend (25%) | EMA stack, ADX, price slope, higher highs/lows |
| Momentum (20%) | RSI, MACD, StochRSI, Rate of Change |
| Volume (15%) | Volume vs average, OBV trend, volume spikes |
| Volatility (10%) | ATR range, BB squeeze, vol contraction |
| Backtest (15%) | How many strategies are profitable on this stock |
| Risk (15%) | Distance from highs, support proximity, risk/reward |

**Verdict:** Score >= 65 = GO, 45-65 = WAIT, < 45 = AVOID

## Swing Screener Scan Types

| Scan | What it detects |
|------|----------------|
| Breakout | Price crossing 20 EMA or 20-day high with volume |
| Reversal | RSI oversold bounce in uptrending stock |
| Squeeze | Bollinger Band squeeze about to expand |
| Volume Surge | Unusual volume spike with bullish candle |
| Supertrend Flip | Supertrend indicator turning bullish |
| Trend Continuation | Strong established uptrend with pullback entry |

## Built-in Strategies

| Strategy | Type | Best For | Key Parameters |
|----------|------|----------|---------------|
| `sma_crossover` | Trend | Strong trending stocks | fast=20, slow=50 |
| `ema_filtered` | Trend + Filter | Uptrending large caps | fast=9, slow=21, trend=200 |
| `rsi_mean_reversion` | Mean Reversion | Range-bound quality stocks | period=14, oversold=30 |
| `bollinger_breakout` | Breakout | Post-consolidation moves | period=20, std=2.0 |
| `macd_rsi` | Combo | Confirmed momentum | MACD + RSI>50 filter |
| `supertrend` | Trend | Popular in Indian markets | period=10, multiplier=3.0 |
| `momentum` | Momentum | Sector leaders | lookback=90, hold=20 |
| `volume_breakout` | Breakout | Institutional buying | period=20, vol_mult=2.0 |

## Adding Your Own Strategy

Create a file in `custom_strategies/` following this pattern:

```python
import pandas as pd

def my_strategy(df: pd.DataFrame, **params) -> pd.DataFrame:
    """
    Your strategy logic here.
    Must add a 'signal' column: 1=Buy, -1=Sell, 0=Hold
    """
    data = df.copy()
    # ... your logic ...
    data["signal"] = 0
    # Set buy/sell conditions
    data.loc[buy_condition, "signal"] = 1
    data.loc[sell_condition, "signal"] = -1
    data["strategy_name"] = "My Strategy"
    return data
```

Then register it in `nse_backtest/strategies.py`:
```python
from custom_strategies.example_strategy import my_strategy
STRATEGIES["my_strategy"] = my_strategy
```

## Transaction Cost Model

The engine uses **Zerodha delivery trading costs** by default and switches to
intraday rates automatically when ``TradeConfig.trading_mode = "INTRADAY"``:

- Brokerage: ₹0 (delivery) / min(₹20, 0.03% turnover) per order (intraday)
- STT: 0.1% sell (delivery), 0.025% sell (intraday)
- Stamp duty: 0.015% buy (delivery), 0.003% buy (intraday)
- GST: 18% on brokerage + transaction charges
- Slippage: 0.1% assumed
- DP charge: ₹15.93 per delivery sell

**MTF mode** (Margin Trade Facility): set ``trading_mode="MTF"`` — adds an
interest accrual on the borrowed portion (default 18% p.a.) per holding day,
and applies a leverage factor configurable via ``mtf_margin_pct``. The MTF
position tracker page in the UI projects breakeven drift and exit scenarios.

You can customize in `TradeConfig`:
```python
from nse_backtest.engine import TradeConfig

config = TradeConfig(
    initial_capital=200_000,
    trading_mode="INTRADAY",  # or "DELIVERY" / "MTF"
    stop_loss_pct=0.07,       # 7% stop loss
    take_profit_pct=0.15,     # 15% target
    position_pct=0.5,         # Use 50% of capital per trade
)
```

### Environment Variables

- ``NSE_LOG_LEVEL`` — logging level for ``nse_backtest`` (DEBUG / INFO / WARNING / ERROR). Default: INFO.
- ``NSE_LOG_FILE`` — optional path; if set, logs are also written to this file.

## Key Metrics Explained

- **Sharpe Ratio**: Risk-adjusted return. Above 1.0 = good, above 1.5 = excellent
- **Max Drawdown**: Worst peak-to-trough decline. Keep under 25% ideally
- **Profit Factor**: Gross wins / gross losses. Above 2.0 = strong edge
- **Win Rate**: % of profitable trades. Context-dependent (low WR + high avg win = fine)
- **Expectancy**: Average ₹ profit per trade. Must be positive for edge

## Roadmap

- [x] Core backtesting engine with Zerodha costs (incl. STT, IPFT, GST, stamp duty)
- [x] 11 built-in strategies
- [x] Strategy comparison and ranking
- [x] Equity curves, drawdown charts, metrics
- [x] Daily stock screener (6 scan types: breakout, reversal, squeeze, volume surge, supertrend flip, trend continuation)
- [x] MTF position risk dashboard (with leverage + interest accrual)
- [x] Multi-stock portfolio backtesting
- [x] 3-round 16-expert adversarial security/correctness audit (61 regression tests)
- [ ] Kite Connect integration for live alerts
- [ ] Walk-forward optimization
- [ ] Sector rotation strategy

## Important Disclaimer

This is a **research and education tool**. Past performance does not guarantee future results. Always paper-trade strategies before using real money. I am not a financial advisor — this is a systematic approach to testing ideas, not investment advice.

## License

MIT — use freely, modify as needed.
