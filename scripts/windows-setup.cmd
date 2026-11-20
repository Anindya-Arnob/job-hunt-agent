@echo off
REM Runs windows-setup.ps1 in Windows PowerShell with its own module path (a module path inherited from
REM PowerShell 7 breaks Windows PowerShell). Used by the installer; you can also run it to repair the app.
set PSModulePath=
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows-setup.ps1" %*
exit /b %errorlevel%
