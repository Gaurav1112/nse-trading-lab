# Vendored review agents (project-scoped)

Source: https://github.com/affaan-m/ECC (MIT), pinned to commit `b92f181b3b748d1abbc2afd94af6aa7d8c8fca29`
(copied 2026-10-29 from the same pin used in investment-intelligence-pro). Licence text is in `LICENSE-ECC`
and must stay with these files.

Only three read-only review roles were adopted: `python-reviewer`, `security-reviewer`, `silent-failure-hunter`.
ECC's installer, hooks, MCP configs and global rules were deliberately NOT installed: hooks would run in every
Claude Code session on this machine.

Project rules that override these definitions when they are used here:
- Do not run `npx`, `npm install`, `pip install` or anything that downloads code. Use the project's own gate:
  `python3.13 -m pytest`.
- Read-only: report findings with file:line and a reproduction; do not edit code.
- Real-money app. The trade desk must never place an order, must stay deterministic (no LLM in the signal path),
  and must fail loud (WARN/BLOCK) rather than silently pass when data is missing.

To update: re-fetch from a reviewed ECC commit and update the pin above.
