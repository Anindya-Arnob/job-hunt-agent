#!/usr/bin/env bash
# Starts Job Hunt Agent and opens it in your browser. Press Ctrl+C (or close the Terminal window) to stop.
cd "$(dirname "$0")/../backend"
if [ ! -f ../.venv/bin/activate ]; then
  echo "Not installed yet: run ./scripts/install.sh first."; exit 1
fi
source ../.venv/bin/activate
# Python from python.org on macOS has no certificates of its own: use certifi's (GitHub, Gmail, job sites)
export SSL_CERT_FILE="${SSL_CERT_FILE:-$(python -c 'import certifi; print(certifi.where())' 2>/dev/null)}"
export PATH="$HOME/.local/bin:$PATH"        # where Claude Code's installer puts `claude`
python -m jobagent "$@"
