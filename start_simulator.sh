#!/usr/bin/env bash
# Vesti-Care — Run Simulator (macOS / Linux)
# Usage:
#   bash start_simulator.sh              -> one_fall (default)
#   bash start_simulator.sh normal
#   bash start_simulator.sh one_fall
#   bash start_simulator.sh multi_fall
#   bash start_simulator.sh bad_payload
#   bash start_simulator.sh all

set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "[ERROR] Virtual environment not found. Run: bash setup.sh"
    exit 1
fi

source .venv/bin/activate

SCENARIO=${1:-one_fall}

if [ "$SCENARIO" = "all" ]; then
    echo "Running all scenarios..."
    python backend/simulator/run_scenario.py --all
else
    echo "Running scenario: $SCENARIO"
    python backend/simulator/run_scenario.py --scenario "$SCENARIO"
fi
