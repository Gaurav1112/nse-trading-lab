---
name: trade-desk
description: >-
  NSE + US trade desk for this repo. Turns a stock into a sized TAKE / WAIT / SKIP decision with
  stop, targets, Monte Carlo odds, setup history and 17 risk checks; also the daily plan, position
  review and intraday opening-range scan. Use when the user says "check RELIANCE", "should I buy",
  "size this trade", "what should I trade today", "review my positions", "any intraday setups",
  "set my capital / max loss", "is the desk working?", "how have the desk's picks done?". Never places
  orders.
argument-hint: "[check SYMBOL [--qty N] [--choose] [--side short] | plan | positions | intraday | settings | scoreboard | shortcheck] [--market NSE|US]"
---

# trade-desk

Run from the repo root with `python3.13 -m nse_backtest.desk <command>`:

    check RELIANCE --market NSE                     # default swing plan at last close
    check AAPL --market US --entry 231 --stop 226   # the user's own plan
    check INFY --mode intraday                      # opening-range breakout (stop-limit entry)
    check INFY --mode intraday --side short         # ORB breakdown short (NSE F&O, HOSTILE days, MIS)
    shortcheck                                      # pooled ORB-short history: may shorts be a TAKE today?
    check RELIANCE --qty 20                         # money for the user's own quantity
    check RELIANCE --choose                         # user takes a WAIT/SKIP anyway; logged as an override
    check RELIANCE --desk-entry                     # swing at the plan's own entry (breakout above the 20-day high)
    plan --market NSE                               # today's plan: regime, TAKE, WATCH, why symbols were skipped
    plan --market NSE --all                         # plus every other candidate, ranked, with the money
    positions --market US
    intraday --market NSE
    settings --market NSE --capital 750000 --risk-pct 1
    scoreboard                                      # paper record of logged TAKEs, all markets
    scoreboard --market US --json

A short is at most WAIT unless `shortcheck` (cached for the day in the data dir) shows at least 200 pooled ORB-short
trades on HOSTILE days with a positive expectancy at 2× slippage; without today's figure the short_edge check reads
"edge check unavailable". Run `shortcheck` before calling a short a TAKE.

Use `scoreboard` when the user asks whether the desk is working or whether its odds hold up. It is
read-only: it replays the TAKEs in `desk_audit.jsonl` against later prices (conservative fills) and
caches final results in `desk_scoreboard.json`; it writes no trades and places nothing. It is the
desk's paper record, not the user's real P&L. Report the verdict per market and mode first (needs 30
resolved trades; Losing / Worse than the desk's odds / Matching the odds / Inconclusive), then n, mean
realised R vs predicted MC R and the 95% interval, then the count of `data_unavailable` trades. ORB shorts are
scored separately as "intraday short" (mirrored rules) and must never be added to the long figures.
Never tell the user to delete `desk_scoreboard.json`: intraday results older than about 60 days exist
only there.

`check` prints a Money block: max loss (intraday: at a fill up to the stop-limit's limit, with a second line
for a fill at the trigger), profit at T1 and T2, reward:risk and expected value for the quantity (the desk's, or `--qty N`; limit breaches are listed). If the desk sized 0 shares it shows the
per-share figures and asks for `--qty`. `--choose` only on the user's explicit say-so: it prints the
override ticket for a WAIT or SKIP and logs it as the user's override (scored apart from the desk's
picks). It does nothing for a TAKE. Intraday tickets are stop-limit orders (a plain limit at the same limit price
when the price is already at or above the trigger) with a "cancel the entry if not filled by" time; relay
that line.

Rules for Claude:
- When presenting a trade, give the money first: max loss, then profit at T1 and T2 (and say a gap can
  lose more than the max loss). Then the verdict line, every BLOCK and WARN in plain words, then the ticket.
- Never soften a SKIP or a negative expectancy. "No trade" is a valid answer; say what would change it.
- Never place, modify or cancel an order, and never call a broker order tool. The user types the
  ticket into Kite or their US broker themselves.
- Lead with expectancy in R, not win rate.
- Quick verdicts in `plan --all`/`--json` (first_pass) need `check SYM --entry X` before relaying anything.
- If `plan` prints "PLAN FAILED for every symbol", say the desk is broken today, not that there is no trade.
- If the user asks for portfolio context and the `kite` MCP server is connected, use its read-only
  holdings/positions tools only.
- Not financial advice; the odds are a reality check from the stock's own history, not a forecast.
