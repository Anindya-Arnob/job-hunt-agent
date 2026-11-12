#!/usr/bin/env bash
# Job Hunt Agent: one-step install for macOS (and Linux). Installs everything it needs, without admin rights:
#   Python (via uv), the app's packages, the browser for the job-site agents, LibreOffice for PDFs,
#   Claude Code, and a "Job Hunt Agent" icon (Applications + Desktop) that starts the app.
#
# On a Mac, open Terminal and paste:
#   curl -fsSL https://raw.githubusercontent.com/Anindya-Arnob/job-hunt-agent/main/scripts/setup.sh | bash
# Run the same line again later to update: your CV, jobs, settings and documents are kept.
set -euo pipefail

main() {
REPO="Anindya-Arnob/job-hunt-agent"
BRANCH="${JHA_BRANCH:-main}"
OS="$(uname)"
step=0
say()  { step=$((step + 1)); printf '\n\033[1;34m[%s] %s\033[0m\n' "$step" "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '    \033[33m%s\033[0m\n' "$*"; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
export PATH="$HOME/.local/bin:$PATH"
# Prompts must read the keyboard even when this script arrives through `curl | bash`; with no keyboard at all
# (an assistant or a test running it) nothing waits for one
TTY=""; if ( exec </dev/tty ) 2>/dev/null; then TTY=/dev/tty; fi

# ---------------------------------------------------------------- the app's files ---
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$HERE" ] && [ -f "$HERE/../backend/requirements.txt" ]; then
  DEST="$(cd "$HERE/.." && pwd)"                       # run from a downloaded / cloned copy: install it in place
  say "Installing Job Hunt Agent in $DEST"
else
  DEST="${JHA_HOME:-$HOME/JobHuntAgent}"
  say "Downloading Job Hunt Agent to $DEST"
  curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" | tar -xz -C "$TMP"
  mkdir -p "$DEST"
  # update in place; never touch your data, settings or documents
  (cd "$TMP"/*-"$BRANCH" && tar --exclude=./data --exclude=./.env --exclude=./.venv --exclude=./sample_job_dir -cf - .) \
    | (cd "$DEST" && tar -xf -)
  chmod +x "$DEST"/scripts/*.sh "$DEST"/scripts/*.command 2>/dev/null || true
fi
cd "$DEST"

# ---------------------------------------------------------------- Python + packages ---
say "Python and the app's packages (a few minutes the first time)"
PYBIN=""
if ! command -v uv >/dev/null 2>&1; then
  note "Getting uv (installs Python for this app only, no admin needed) ..."
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null 2>&1 || warn "uv could not be installed."
fi
if command -v uv >/dev/null 2>&1; then
  [ -x .venv/bin/python ] || uv venv --quiet --python 3.12 .venv
  uv pip install --quiet --python .venv/bin/python -r backend/requirements.txt
  PYBIN=".venv/bin/python"
else
  for c in python3.13 python3.12 python3.11 python3; do       # fallback: a Python 3.11+ already on this computer
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      [ -x .venv/bin/python ] || "$c" -m venv .venv
      .venv/bin/python -m pip install --quiet --upgrade pip
      .venv/bin/python -m pip install --quiet -r backend/requirements.txt
      PYBIN=".venv/bin/python"; break
    fi
  done
fi
if [ -z "$PYBIN" ]; then
  warn "Could not get Python 3.11+. Install it from https://www.python.org/downloads/ and run this again."
  exit 1
fi
note "$($PYBIN --version) ready."

say "The browser the job-site agents use"
$PYBIN -m playwright install chromium >/dev/null 2>&1 && note "Chromium ready." || warn "Chromium download failed: run this setup again later."

[ -f .env ] || cp .env.example .env

# ---------------------------------------------------------------- Word -> PDF ---
say "Word to PDF (for your CVs and letters)"
lo_app() { for p in /Applications/LibreOffice.app "$HOME/Applications/LibreOffice.app"; do [ -d "$p" ] && return 0; done; return 1; }
if command -v soffice >/dev/null 2>&1 || lo_app; then
  note "LibreOffice found."
elif [ "$OS" = "Darwin" ] && [ -d "/Applications/Microsoft Word.app" ]; then
  note "Microsoft Word found. The first PDF asks once to let Terminal control Word: click OK."
elif [ "$OS" = "Darwin" ]; then
  if [ -z "${JHA_NO_BREW:-}" ] && command -v brew >/dev/null 2>&1 && brew install --cask libreoffice >/dev/null 2>&1; then
    note "LibreOffice installed (Homebrew)."
  else
    note "Downloading LibreOffice (free, about 300 MB) ..."
    BASE="https://download.documentfoundation.org/libreoffice/stable"
    ARCH="$( [ "$(uname -m)" = "arm64" ] && echo aarch64 || echo x86_64 )"
    VER="$(curl -fsSL "$BASE/" | grep -oE 'href="[0-9]+\.[0-9]+\.[0-9]+/"' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | sort -t. -k1,1n -k2,2n -k3,3n | tail -1 || true)"
    DMG="$(curl -fsSL "$BASE/$VER/mac/$ARCH/" 2>/dev/null | grep -oE 'LibreOffice_[^"<>]*\.dmg' | grep -viE 'helppack|langpack' | head -1 || true)"
    if [ -n "$VER" ] && [ -n "$DMG" ] && curl -fL --progress-bar -o "$TMP/lo.dmg" "$BASE/$VER/mac/$ARCH/$DMG"; then
      MNT="$(hdiutil attach -nobrowse -readonly "$TMP/lo.dmg" | grep -oE '/Volumes/.*' | head -1)"
      mkdir -p "$HOME/Applications"
      cp -R "$MNT/LibreOffice.app" "$HOME/Applications/" && note "LibreOffice $VER installed in your Applications folder."
      hdiutil detach "$MNT" -quiet || true
    else
      warn "LibreOffice could not be downloaded. Get it later from https://www.libreoffice.org (PDFs wait until then)."
    fi
  fi
else
  warn "Install LibreOffice for PDFs:  sudo apt install libreoffice-writer   (or your distribution's package)"
fi

# ---------------------------------------------------------------- Claude Code ---
say "Claude Code (the app uses your Claude subscription through it)"
CLAUDE="$(command -v claude || true)"; [ -z "$CLAUDE" ] && [ -x "$HOME/.local/bin/claude" ] && CLAUDE="$HOME/.local/bin/claude"
if [ -z "$CLAUDE" ]; then
  curl -fsSL https://claude.ai/install.sh | bash >/dev/null 2>&1 || true
  CLAUDE="$(command -v claude || echo "$HOME/.local/bin/claude")"
fi
if [ -x "$CLAUDE" ]; then
  note "Claude Code $("$CLAUDE" --version 2>/dev/null | head -1) ready."
  if ! "$CLAUDE" auth status 2>/dev/null | grep -q '"loggedIn": *true'; then
    if [ -n "$TTY" ]; then
      note "Log in with your Claude Pro / Max account: your browser opens now."
      "$CLAUDE" auth login <"$TTY" || warn "Not logged in yet: the app shows a 'Log in again' button."
    else
      warn "Not logged in to Claude yet: run 'claude auth login' in Terminal, or press 'Log in again' in the app."
    fi
  else
    note "Already logged in."
  fi
else
  warn "Claude Code could not be installed. The app still runs; install it later from https://claude.ai/code"
fi

# ---------------------------------------------------------------- the icon ---
say "A 'Job Hunt Agent' icon to start the app"
if [ "$OS" = "Darwin" ]; then
  mkdir -p "$HOME/Desktop"
  LAUNCH="$HOME/Desktop/Job Hunt Agent.command"
  printf '#!/bin/bash\n# Starts Job Hunt Agent. Close this window to stop it.\nexec "%s/scripts/start.sh"\n' "$DEST" > "$LAUNCH"
  chmod +x "$LAUNCH"
  # give it the app's picture instead of the plain script icon (best effort)
  osascript -l JavaScript -e "ObjC.import('AppKit'); \$.NSWorkspace.sharedWorkspace.setIconForFileOptions(\$.NSImage.alloc.initWithContentsOfFile('$DEST/scripts/job-hunt-agent.png'), '$LAUNCH', 0)" >/dev/null 2>&1 || true
  note "Double-click 'Job Hunt Agent' on your Desktop to start it."
  note "A Terminal window shows it is running: close that window to stop the app."
else
  LAUNCH="$DEST/scripts/start.sh"
  mkdir -p "$HOME/.local/share/applications"
  printf '[Desktop Entry]\nType=Application\nName=Job Hunt Agent\nExec=%s/scripts/start.sh\nIcon=%s/scripts/job-hunt-agent.png\nTerminal=true\n' "$DEST" "$DEST" \
    > "$HOME/.local/share/applications/job-hunt-agent.desktop"
  note "Start it from your app menu ('Job Hunt Agent') or with $DEST/scripts/start.sh"
fi

# ---------------------------------------------------------------- done ---
say "Done"
note "The browser helper (for filling application forms) is the one step Chrome makes you do by hand:"
note "  Chrome -> chrome://extensions -> switch on Developer mode -> Load unpacked -> choose:"
note "  $DEST/extension"
if [ "$OS" = "Darwin" ] && [ -z "${JHA_NO_START:-}" ]; then
  open -R "$DEST/extension" 2>/dev/null || true
  open "$LAUNCH"
fi
}

main "$@" </dev/null
