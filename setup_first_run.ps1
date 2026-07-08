# ============================================================================
# setup_first_run.ps1 - interactive first-run bootstrap for market_data_hub
#
# Run from PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\setup_first_run.ps1
#
# This script is intentionally idempotent: it can be re-run after pulling the
# repo on a new machine or after changing local secrets.
# ============================================================================
param(
    [string]$Python = "C:\ProgramData\spyder-6\python.exe",
    [string]$DbPath = "market_data.duckdb",
    [switch]$SkipInstall,
    [switch]$SkipTests,
    [switch]$RunBackfill,
    [switch]$ConfigureScheduler
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Read-OptionalSecret($Prompt, $ExistingLabel) {
    $current = [Environment]::GetEnvironmentVariable($ExistingLabel, "User")
    if ($current) {
        $answer = Read-Host "$Prompt already set. Press Enter to keep it, or paste a new value"
    } else {
        $answer = Read-Host "$Prompt (press Enter to skip)"
    }
    if ($answer) {
        [Environment]::SetEnvironmentVariable($ExistingLabel, $answer, "User")
        Set-Item -Path "Env:$ExistingLabel" -Value $answer
        Write-Host "Set $ExistingLabel for current user."
    } elseif ($current) {
        Set-Item -Path "Env:$ExistingLabel" -Value $current
        Write-Host "Keeping existing $ExistingLabel."
    } else {
        Write-Host "Skipping $ExistingLabel."
    }
}

Write-Host ""
Write-Host "market_data_hub first-run setup"
Write-Host "Repo: $Root"
Write-Host ""

if (!(Test-Path $Python)) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found) {
        Write-Warning "Configured Python not found: $Python"
        $Python = $found.Source
        Write-Host "Using Python on PATH: $Python"
    } else {
        throw "Python not found. Install Spyder/Python or pass -Python C:\path\python.exe"
    }
}

[Environment]::SetEnvironmentVariable("MARKET_DATA_DB", $DbPath, "User")
Set-Item -Path Env:MARKET_DATA_DB -Value $DbPath
Write-Host "MARKET_DATA_DB=$DbPath"

Read-OptionalSecret "FRED API key" "FRED_API_KEY"
Read-OptionalSecret "Telegram bot token" "TELEGRAM_BOT_TOKEN"
Read-OptionalSecret "Telegram chat id / @channel" "TELEGRAM_CHAT_ID"

if (!$SkipInstall) {
    Write-Host ""
    Write-Host "Installing/updating project dependencies..."
    & $Python -m ensurepip --upgrade
    & $Python -m pip install -e ".[dev]"

    $lazyTools = Join-Path (Split-Path $Root -Parent) "LazyTools"
    if (Test-Path $lazyTools) {
        Write-Host "Installing LazyTools[telegram] from local repo..."
        & $Python -m pip install -e "$lazyTools[telegram]"
    } else {
        Write-Warning "LazyTools repo not found next to market-data-hub; Telegram report script will need lazytoolkit[telegram]."
    }
}

Write-Host ""
Write-Host "Verifying configuration..."
& $Python -c "from market_data_hub.config_loader import get_settings; s=get_settings(); print('db_path=' + str(s.get('db_path'))); print('fred_api_key_present=' + str(bool(s.get('fred_api_key'))))"

if (!$SkipTests) {
    Write-Host ""
    Write-Host "Running test suite..."
    & $Python -m pytest
}

if ($ConfigureScheduler) {
    Write-Host ""
    Write-Host "Creating/updating scheduled tasks..."
    powershell -ExecutionPolicy Bypass -File (Join-Path $Root "setup_scheduler.ps1")
}

if ($RunBackfill) {
    Write-Host ""
    Write-Host "Starting full historical backfill. This can take a long time."
    & $Python -c "from market_data_hub.runner import run; run(mode='backfill', sources=['yahoo','fred','binance','macro_panel','factors'], db_path='$DbPath')"
}

Write-Host ""
Write-Host "First-run setup complete."
Write-Host "Open a new PowerShell/Spyder session to inherit saved user environment variables."
