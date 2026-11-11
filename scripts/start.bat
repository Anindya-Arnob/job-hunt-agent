@echo off
REM Starts Job Hunt Agent and opens it in your browser. Close this window to stop the app.
cd /d "%~dp0\..\backend"
call ..\.venv\Scripts\activate.bat
python -m jobagent
pause
