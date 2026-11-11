#!/usr/bin/env bash
# Starts Job Hunt Agent and opens it in your browser. Press Ctrl+C to stop.
cd "$(dirname "$0")/../backend"
source ../.venv/bin/activate
python -m jobagent "$@"
