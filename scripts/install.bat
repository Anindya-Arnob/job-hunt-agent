@echo off
REM One-time setup for Job Hunt Agent (Windows). Needs Python 3.11+ from python.org.
cd /d "%~dp0\.."
echo.
echo [1/6] Creating a Python environment in .venv ...
py -3 -m venv .venv || python -m venv .venv
if errorlevel 1 ( echo Python not found. Install it from https://www.python.org/downloads/ and tick "Add to PATH". & pause & exit /b 1 )
call .venv\Scripts\activate.bat
echo [2/6] Installing packages (takes a few minutes) ...
python -m pip install --upgrade pip >nul
pip install -r backend\requirements.txt || ( echo Package install failed. & pause & exit /b 1 )
echo [3/6] Installing the browser used by the job-site agents ...
python -m playwright install chromium
echo [4/6] Preparing settings ...
if not exist .env copy .env.example .env >nul
echo [5/6] Checking for Claude Code ...
where claude >nul 2>nul
if errorlevel 1 (
  if exist "%USERPROFILE%\.local\bin\claude.exe" ( echo       Claude Code found. ) else (
    echo       Claude Code not found. To use your Claude subscription, open PowerShell and run:
    echo         irm https://claude.ai/install.ps1 ^| iex
    echo       then run "claude" once and log in with your Claude Pro / Max account.
  )
) else ( echo       Claude Code found. )
echo [6/6] A "Job Hunt Agent" icon on your Desktop ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Job Hunt Agent.lnk')); $s.TargetPath='%CD%\scripts\start.bat'; $s.WorkingDirectory='%CD%\scripts'; $s.IconLocation='%CD%\scripts\job-hunt-agent.ico'; $s.Description='Start Job Hunt Agent (close its window to stop it)'; $s.WindowStyle=7; $s.Save()" >nul 2>nul && echo       Done: double-click "Job Hunt Agent" on your Desktop to start the app.
echo.
echo Done. Double-click "Job Hunt Agent" on your Desktop (or scripts\start.bat) to open the app.
pause
