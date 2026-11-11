@echo off
REM One-time setup for Job Hunt Agent (Windows). Needs Python 3.11+ from python.org.
cd /d "%~dp0\.."
echo.
echo [1/5] Creating a Python environment in .venv ...
py -3 -m venv .venv || python -m venv .venv
if errorlevel 1 ( echo Python not found. Install it from https://www.python.org/downloads/ and tick "Add to PATH". & pause & exit /b 1 )
call .venv\Scripts\activate.bat
echo [2/5] Installing packages (takes a few minutes) ...
python -m pip install --upgrade pip >nul
pip install -r backend\requirements.txt || ( echo Package install failed. & pause & exit /b 1 )
echo [3/5] Installing the browser used by the job-site agents ...
python -m playwright install chromium
echo [4/5] Preparing settings ...
if not exist .env copy .env.example .env >nul
echo [5/5] Checking for Claude Code ...
where claude >nul 2>nul
if errorlevel 1 (
  if exist "%USERPROFILE%\.local\bin\claude.exe" ( echo       Claude Code found. ) else (
    echo       Claude Code not found. To use your Claude subscription, open PowerShell and run:
    echo         irm https://claude.ai/install.ps1 ^| iex
    echo       then run "claude" once and log in with your Claude Pro / Max account.
  )
) else ( echo       Claude Code found. )
echo.
echo Done. Double-click scripts\start.bat to open the app.
pause
