#!/usr/bin/env bash
set -euo pipefail
DEST="${HOME}/.nse-trading-lab/signals-clone"
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" pull --ff-only
else
  mkdir -p "$(dirname "$DEST")"
  git clone "git@github.com:$(gh api user --jq .login)/nse-trading-lab-signals.git" "$DEST"
fi
