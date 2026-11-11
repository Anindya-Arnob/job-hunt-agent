#!/usr/bin/env bash
# One-time setup for Job Hunt Agent (macOS / Linux). Needs Python 3.11+.
set -e
cd "$(dirname "$0")/.."
PY=$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3)
echo "[1/5] Creating a Python environment in .venv (using $PY) ..."
"$PY" -m venv .venv
source .venv/bin/activate
echo "[2/5] Installing packages (takes a few minutes) ..."
python -m pip install --upgrade pip >/dev/null
pip install -r backend/requirements.txt
echo "[3/5] Installing the browser used by the job-site agents ..."
python -m playwright install chromium
echo "[4/5] Preparing settings ..."
[ -f .env ] || cp .env.example .env
echo "[5/5] Checking for Claude Code ..."
if command -v claude >/dev/null 2>&1; then
  echo "      Claude Code found: $(claude --version 2>/dev/null)"
else
  echo "      Claude Code not found. To use your Claude subscription, install it:"
  echo "        curl -fsSL https://claude.ai/install.sh | bash"
  echo "      then run 'claude' once and log in with your Claude Pro / Max account."
fi
echo
echo "Done. Start the app with: scripts/start.sh"
