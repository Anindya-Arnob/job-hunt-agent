#!/usr/bin/env bash
# macOS: double-click in Finder to install (opens Terminal).
cd "$(dirname "$0")/.." && ./scripts/install.sh
echo; read -p "Press Enter to close this window." _
