#!/bin/bash
# NSE Trading Lab — Quick Launch
# Usage: ./start.sh

echo ""
echo "  =================================="
echo "  NSE Trading Lab — Starting UI"
echo "  =================================="
echo ""

# Check if virtual env exists
if [ ! -d "venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv venv
    echo "  Installing dependencies..."
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

echo ""
echo "  Opening http://localhost:8501 in browser..."
echo "  Press Ctrl+C to stop"
echo ""

streamlit run ui.py
