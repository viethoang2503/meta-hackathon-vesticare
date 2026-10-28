#!/usr/bin/env bash
# Vesti-Care — Start Backend (macOS / Linux)

set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "[ERROR] Virtual environment not found. Run: bash setup.sh"
    exit 1
fi

source .venv/bin/activate

if [ ! -f "backend/.env" ]; then
    echo "[WARNING] backend/.env not found. Copying from template..."
    cp backend/.env.example backend/.env
fi

echo "Starting Vesti-Care backend on http://localhost:8000 ..."
echo "API docs: http://localhost:8000/docs"
echo "Press Ctrl+C to stop."
echo
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
