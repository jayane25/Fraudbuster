#!/usr/bin/env sh
# Fraudbusters: one command start for Mac and Linux.  Usage: ./run.sh [port]
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Setting up Python environment, first run only..."
  python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
fi
[ -f data/transactions.csv ] || .venv/bin/python data/generate_synthetic.py
PORT="${1:-8600}"
echo "Fraudbusters Workspace on http://localhost:$PORT  (Ctrl+C to stop)"
.venv/bin/python -m uvicorn app.server:app --port "$PORT"
