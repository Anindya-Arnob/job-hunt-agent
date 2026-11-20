# Job Hunt Agent - Windows setup, run by the installer (JobHuntAgentSetup.exe) after it copied the app.
# Installs everything for you without admin rights: Python (via uv), the app's packages, the browser for the
# job-site agents and Claude Code. Running it again updates the packages and keeps your data and settings.
param([switch]$Unattended)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$App = Split-Path -Parent $PSScriptRoot
$Data = Join-Path $App "data"
New-Item -ItemType Directory -Force -Path $Data | Out-Null
$Log = Join-Path $Data "install-log.txt"
Start-Transcript -Path $Log -Append | Out-Null
$Host.UI.RawUI.WindowTitle = "Job Hunt Agent - setting up"

function Step($n, $text) { Write-Host ""; Write-Host "[$n/6] $text" -ForegroundColor Cyan }
function Ask($question) {
    if ($Unattended) { return $env:JHA_YES -eq "1" }
    return (Read-Host "$question [Y/n]") -notmatch '^(n|no|nein)$'
}
function Done($ok) {
    Stop-Transcript | Out-Null
    if (-not $Unattended) { Write-Host ""; Read-Host "Press Enter to close this window" | Out-Null }
    exit $(if ($ok) { 0 } else { 1 })
}

try {
    Set-Location $App
    Write-Host "Setting up Job Hunt Agent in $App" -ForegroundColor White
    Write-Host "The first time takes 5-10 minutes (downloads about 500 MB). Updates are quicker."

    Step 1 "Python tools (uv)"
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv) {
        $local = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
        if (-not (Test-Path $local)) {
            Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        }
        $uv = $local
    }
    if (-not (Test-Path $uv)) { throw "uv could not be installed (no internet?)" }
    Write-Host "      uv: $uv"

    Step 2 "Python 3.12 and the app's packages"
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        & $uv venv --python 3.12 .venv
        if ($LASTEXITCODE) { throw "Could not create the Python environment" }
    }
    & $uv pip install --python .venv\Scripts\python.exe -r backend\requirements.txt
    if ($LASTEXITCODE) { throw "Installing the packages failed" }

    Step 3 "Browser for the job-site agents (Chromium)"
    & .venv\Scripts\python.exe -m playwright install chromium
    if ($LASTEXITCODE) { throw "Installing Chromium failed" }

    Step 4 "Your settings"
    if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "      created .env" }
    else { Write-Host "      kept your .env" }

    Step 5 "PDF maker (Microsoft Word or LibreOffice)"
    $word = Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Winword.exe"
    $lo = (Test-Path "$env:ProgramFiles\LibreOffice\program\soffice.exe") -or
          (Test-Path "${env:ProgramFiles(x86)}\LibreOffice\program\soffice.exe")
    if ($word) { Write-Host "      Microsoft Word found: CVs become PDFs with Word." }
    elseif ($lo) { Write-Host "      LibreOffice found." }
    else {
        Write-Host "      Neither Word nor LibreOffice is installed. The app needs one of them to turn CVs into PDFs."
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget -and (Ask "      Install LibreOffice (free, about 350 MB) now? Windows may ask for permission.")) {
            winget install -e --id TheDocumentFoundation.LibreOffice
        } else {
            Write-Host "      Later: install LibreOffice from https://www.libreoffice.org/download/ (free)." -ForegroundColor Yellow
        }
    }

    Step 6 "Claude Code (writes your CVs and cover letters with your Claude subscription)"
    $claude = (Get-Command claude -ErrorAction SilentlyContinue).Source
    if (-not $claude -and (Test-Path "$env:USERPROFILE\.local\bin\claude.exe")) { $claude = "$env:USERPROFILE\.local\bin\claude.exe" }
    if ($claude) { Write-Host "      Claude Code found: $claude" }
    elseif ($env:JHA_NO_CLAUDE -eq "1") { Write-Host "      skipped" }
    else {
        Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression
        Write-Host "      Installed. Log in once with your Claude Pro / Max account: open a new PowerShell window," -ForegroundColor Yellow
        Write-Host "      type  claude  and follow the steps in your browser." -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "OK - Job Hunt Agent is ready. Start it with the 'Job Hunt Agent' icon on your Desktop or in the Start menu." -ForegroundColor Green
    Done $true
} catch {
    Write-Host ""
    Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Check your internet connection and run the installer again. Details: $Log"
    Done $false
}
