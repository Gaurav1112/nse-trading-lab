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

exec streamlit run ui.py
