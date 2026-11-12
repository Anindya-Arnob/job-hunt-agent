# Job Hunt Agent: notes for Claude (or any coding assistant)

If someone asks you to install or set up this project for them, follow this. They are usually not a developer.

## Install

- **macOS / Linux:** run one command. It installs everything without admin rights (Python via uv, the packages,
  Chromium for the site agents, LibreOffice for PDFs when neither LibreOffice nor Word is present, Claude Code),
  creates a "Job Hunt Agent" icon on the Desktop and starts the app:
  ```bash
  curl -fsSL https://raw.githubusercontent.com/Anindya-Arnob/job-hunt-agent/main/scripts/setup.sh | bash
  ```
  Or, inside a clone: `./scripts/setup.sh`. Running it again updates the app and keeps `data/`, `.env` and documents.
  The app is installed to `~/JobHuntAgent` (override with `JHA_HOME=...`). Set `JHA_NO_START=1` to skip starting it.
- **Windows:** install Python 3.12 from python.org (tick "Add python.exe to PATH"), then double-click
  `scripts\install.bat`, then `scripts\start.bat`. Claude Code: `irm https://claude.ai/install.ps1 | iex` in PowerShell.

The app runs at http://127.0.0.1:8780 and opens in the browser.

## What the person must do themselves

- **Log in to Claude Code** with their own Claude Pro / Max account (`claude auth login`; the browser opens).
  If you are Claude Code yourself, it is already installed and logged in: the setup skips this.
- **The Chrome helper** (only for filling application forms): Chrome → `chrome://extensions` → Developer mode →
  Load unpacked → the project's `extension` folder. Chrome does not allow installing it any other way.
- **Their own data** in the app: upload their CV on *My CV*, fill in *My details*, set up searches on *Search*.
  Optional: Gmail app password on *Inbox*.

## Don't

- Don't put the person's passwords, Gmail app password or API keys anywhere but the app's own screens or `.env`.
- Don't commit `data/`, `.env` or anything under `sample_job_dir/` (personal data).
- Don't change the app's code to make an install step pass; report what failed instead.

## Checking it worked

- `curl -s http://127.0.0.1:8780/api/status` returns JSON; `"pdf_available": true` means CV PDFs will work.
- Tests: `cd backend && ../.venv/bin/python -m pytest -q` (about 3 minutes, all should pass).

How the system works: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
