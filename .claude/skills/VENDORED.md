# Vendored skills

## trader-news, trader-analyst (US research)

- Source: https://github.com/Jakeschincariol/day-trader-skill (MIT, see `LICENSE-day-trader-skill`)
- Commit: `3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd`
- Vendored: `trader-news/` and `trader-analyst/` only (SKILL.md plus the Python files).
- Audit: 2026-10-29, verdict **SAFE-WITH-NOTES**. The code was read, not executed.

### Audit notes

- N1: `trader-news` fetches headlines from the `feeds.finance.yahoo.com` RSS host (an unofficial
  Yahoo endpoint) and prices from `query1/2.finance.yahoo.com`. These are network calls to third parties.
- N2: tickers were not validated before being placed in URLs. **Fixed here**: `news.py` now checks
  every ticker with `re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", t)` (`validate_ticker`), rejects with a clear
  error, and `urllib.parse.quote`s it in URLs. Local change; reapply it if you re-vendor.
- N7: `trader-analyst/facts.py` `--cik` was only zero-filled before going into SEC URLs. **Fixed here**: `main()`
  now requires `re.fullmatch(r"\d{1,10}", args.cik)` and exits 2 otherwise. Local change; reapply it if you re-vendor.
- N8: both `SKILL.md` files said "Nothing places an order except `/trader-broker` ...", pointing at a skill
  that is not vendored. **Changed here** to "This repo never places orders; there is no broker skill."
  The `trader-news` input table also said "trader-broker records fills there"; reworded to "you record
  fills there yourself".
  Local change; reapply it if you re-vendor.
- N3: feeds are parsed with `xml.etree.ElementTree`. Acceptable here (trusted hosts, no DTD
  resolution); noted for completeness.
- N4: `trader-broker` has a chat "yes" confirmation gate that can be spoofed. **Not vendored.**
- N5: `trader-broker` runs an unpinned `uvx alpaca-mcp-server`. **Not vendored.**
- N6: `trader-pelosi` `ptr.py` builds a path from a DocID. **Not vendored.**

### Rule

These skills are research context in Claude sessions only. They are never inputs to a desk verdict:
nothing under `nse_backtest/desk/` imports, runs or reads their output.
