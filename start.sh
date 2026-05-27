#!/bin/bash
# ============================================================
# start.sh — One command to start the Tasty Pick Ems backend
#
# Run this in Terminal from the tasty-pick-ems folder:
#   chmod +x start.sh   (only needed once to make it executable)
#   ./start.sh
# ============================================================

# Move to the folder this script lives in
cd "$(dirname "$0")"

echo ""
echo "=================================================="
echo "  TASTY PICK EMS — Starting backend server"
echo "=================================================="
echo ""

# Check if .env exists; if not, create it from the example
if [ ! -f ".env" ]; then
    echo "  No .env file found. Creating from .env.example..."
    cp .env.example .env
    echo "  ✓ Created .env — add your API keys to it when ready."
    echo ""
fi

# Install Python dependencies quietly
echo "  Installing dependencies..."
/usr/bin/python3 -m pip install -r requirements.txt --quiet
echo "  ✓ Dependencies ready."
echo ""

# Start the Flask server
/usr/bin/python3 server.py
