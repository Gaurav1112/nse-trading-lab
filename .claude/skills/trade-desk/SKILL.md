---
name: trade-desk
description: >-
  NSE + US trade desk for this repo. Turns a stock into a sized TAKE / WAIT / SKIP decision with
  stop, targets, Monte Carlo odds, setup history and 17 risk checks; also the daily plan, position
  review and intraday opening-range scan. Use when the user says "check RELIANCE", "should I buy",
  "size this trade", "what should I trade today", "review my positions", "any intraday setups",
  "set my capital / max loss", "is the desk working?", "how have the desk's picks done?". Never places
  orders.
argument-hint: "[check SYMBOL [--qty N] [--choose] | plan | positions | intraday | settings | scoreboard] [--market NSE|US]"
---

# trade-desk

Run from the repo root with `python3.13 -m nse_backtest.desk <command>`:

    check RELIANCE --market NSE                     # default swing plan at last close
    check AAPL --market US --entry 231 --stop 226   # the user's own plan
    check INFY --mode intraday                      # opening-range breakout (stop-limit entry)
    check RELIANCE --qty 20                         # money for the user's own quantity
    check RELIANCE --choose                         # user takes a WAIT/SKIP anyway; logged as an override
    plan --market NSE                               # today's plan: regime, TAKE, WATCH
    positions --market US
    intraday --market NSE
    settings --market NSE --capital 750000 --risk-pct 1
    scoreboard                                      # paper record of logged TAKEs, all markets
    scoreboard --market US --json

Use `scoreboard` when the user asks whether the desk is working or whether its odds hold up. It is
read-only: it replays the TAKEs in `desk_audit.jsonl` against later prices (conservative fills) and
caches final results in `desk_scoreboard.json`; it writes no trades and places nothing. It is the
desk's paper record, not the user's real P&L. Report the verdict per market and mode first (needs 30
resolved trades; Losing / Worse than the desk's odds / Matching the odds / Inconclusive), then n, mean
realised R vs predicted MC R and the 95% interval, then the count of `data_unavailable` trades.
Never tell the user to delete `desk_scoreboard.json`: intraday results older than about 60 days exist
only there.

`check` prints a Money block: max loss, profit at T1 and T2, reward:risk and expected value for the
quantity (the desk's, or `--qty N`; limit breaches are listed). If the desk sized 0 shares it shows the
per-share figures and asks for `--qty`. `--choose` only on the user's explicit say-so: it prints the
override ticket for a WAIT or SKIP and logs it as the user's override (scored apart from the desk's
picks). It does nothing for a TAKE. Intraday tickets are stop-limit orders with a "cancel the entry if not
filled by" time; relay that line.

Rules for Claude:
- When presenting a trade, give the money first: max loss, then profit at T1 and T2 (and say a gap can
  lose more than the max loss). Then the verdict line, every BLOCK and WARN in plain words, then the ticket.
- Never soften a SKIP or a negative expectancy. "No trade" is a valid answer; say what would change it.
- Never place, modify or cancel an order, and never call a broker order tool. The user types the
  ticket into Kite or their US broker themselves.
- Lead with expectancy in R, not win rate.
- If the user asks for portfolio context and the `kite` MCP server is connected, use its read-only
  holdings/positions tools only.
- Not financial advice; the odds are a reality check from the stock's own history, not a forecast.
