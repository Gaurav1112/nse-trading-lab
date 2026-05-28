#!/bin/bash
# NSE Trading Lab — Quick Launch
# Usage: ./start.sh
set -euo pipefail

echo ""
echo "  =================================="
echo "  NSE Trading Lab — Starting UI"
echo "  =================================="
echo ""

cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv venv
    # shellcheck disable=SC1091
    source venv/bin/activate
    echo "  Installing dependencies..."
    pip install --upgrade pip
    pip install -r requirements.txt
else
    # shellcheck disable=SC1091
    source venv/bin/activate
fi

echo ""
echo "  Opening http://127.0.0.1:8501 in browser..."
echo "  Press Ctrl+C to stop"
echo ""

# Free the port if a stale streamlit is hogging it (common cause of
# "site can't be reached" — the new instance never binds).
if command -v lsof >/dev/null 2>&1; then
    PIDS=$(lsof -nP -iTCP:8501 -sTCP:LISTEN -t 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        echo "  Port 8501 is busy (pid=$PIDS) — releasing it..."
        kill $PIDS 2>/dev/null || true
        sleep 1
    fi
fi

# Skip the first-run email prompt that blocks the server from starting.
mkdir -p "$HOME/.streamlit"
if [ ! -f "$HOME/.streamlit/credentials.toml" ]; then
    printf '[general]\nemail = ""\n' > "$HOME/.streamlit/credentials.toml"
fi

exec streamlit run Home.py \
    --server.headless=true \
    --server.address=127.0.0.1 \
    --server.port=8501 \
    --browser.gatherUsageStats=false
