#!/usr/bin/env bash
# One-time setup for Job Hunt Agent (macOS / Linux). Needs Python 3.11 or newer.
set -e
cd "$(dirname "$0")/.."

# ---- a Python that is new enough (macOS's own python3 is 3.9: too old) ----
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY=$(command -v "$c"); break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11 or newer is needed (found: $(python3 --version 2>/dev/null || echo none))."
  if [ "$(uname)" = "Darwin" ]; then
    echo "Install it with Homebrew:   brew install python@3.12"
    echo "or from https://www.python.org/downloads/macos/ , then run this script again."
  else
    echo "Install python3.12 with your package manager, then run this script again."
  fi
  exit 1
fi

echo "[1/6] Creating a Python environment in .venv (using $PY, $("$PY" --version)) ..."
"$PY" -m venv .venv
source .venv/bin/activate
echo "[2/6] Installing packages (takes a few minutes) ..."
python -m pip install --upgrade pip >/dev/null
pip install -r backend/requirements.txt
echo "[3/6] Installing the browser used by the job-site agents ..."
python -m playwright install chromium
echo "[4/6] Preparing settings ..."
[ -f .env ] || cp .env.example .env

echo "[5/6] Checking Word -> PDF ..."
if command -v soffice >/dev/null 2>&1 || [ -x /Applications/LibreOffice.app/Contents/MacOS/soffice ] \
   || [ -x "$HOME/Applications/LibreOffice.app/Contents/MacOS/soffice" ]; then
  echo "      LibreOffice found."
elif [ -d "/Applications/Microsoft Word.app" ]; then
  echo "      Microsoft Word found (the first PDF asks you once to let Terminal control Word: click OK)."
else
  echo "      Neither LibreOffice nor Word found: CVs can't be turned into PDFs yet."
  if [ "$(uname)" = "Darwin" ]; then
    echo "      Install LibreOffice (free):  brew install --cask libreoffice   or https://www.libreoffice.org"
  else
    echo "      Install LibreOffice:  sudo apt install libreoffice-writer   (or your distribution's package)"
  fi
fi

echo "[6/6] Checking for Claude Code ..."
if command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ]; then
  echo "      Claude Code found: $( (command -v claude >/dev/null && claude --version) || "$HOME/.local/bin/claude" --version 2>/dev/null)"
else
  echo "      Claude Code not found. To use your Claude subscription, install it:"
  echo "        curl -fsSL https://claude.ai/install.sh | bash"
  echo "      then open a new Terminal, run 'claude' once and log in with your Claude Pro / Max account."
fi
echo
echo "Done. Start the app with:  ./scripts/start.sh"
