#!/usr/bin/env bash
# Vesti-Care — First-time Setup (macOS / Linux)
# Run once after cloning: bash setup.sh

set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  Vesti-Care — Environment Setup"
echo "============================================"
echo

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 not found. Install Python 3.12 first."
    exit 1
fi
echo "[OK] $(python3 --version) found."

# Create virtual environment
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    echo "[OK] Virtual environment created at .venv/"
else
    echo "[OK] Virtual environment already exists."
fi

# Activate
source .venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -r requirements.txt --quiet
echo "[OK] Dependencies installed."

# Set up .env
if [ ! -f "backend/.env" ]; then
    cp backend/.env.example backend/.env
    echo "[OK] Created backend/.env from template."
    echo "     Edit backend/.env to add your Telegram bot token and chat ID."
else
    echo "[OK] backend/.env already exists."
fi

echo "============================================"
echo "  Setup complete."
echo
echo "  Next steps:"
echo "    1. Edit backend/.env  (add Telegram credentials if needed)"
echo "    2. bash start_backend.sh  to start the server"
echo "    3. Open dashboard/index.html in your browser"
echo "    4. bash start_simulator.sh to stream test data"
echo "============================================"
