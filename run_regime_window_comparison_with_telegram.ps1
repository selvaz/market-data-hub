# market_data_hub regime window comparison (full-history vs 8y) -- pure
# read against lazystats_depot, no market_data.duckdb access.
# Requires environment variables:
#   LAZYSTATS_RESULT_DEPOT_DB
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = 'C:\ProgramData\spyder-6\python.exe'

Set-Location $Root

function Import-PersistedEnvVar($Name) {
    if (Test-Path "Env:$Name") {
        return
    }
    $value = [Environment]::GetEnvironmentVariable($Name, "User")
    if (!$value) {
        $value = [Environment]::GetEnvironmentVariable($Name, "Machine")
    }
    if ($value) {
        Set-Item -Path "Env:$Name" -Value $value
        Write-Host "[$(Get-Date -Format s)] Loaded $Name from persisted environment."
    }
}

Import-PersistedEnvVar "LAZYSTATS_RESULT_DEPOT_DB"
Import-PersistedEnvVar "TELEGRAM_BOT_TOKEN"
Import-PersistedEnvVar "TELEGRAM_CHAT_ID"

Write-Host "[$(Get-Date -Format s)] Starting regime window comparison (8y)"
& $Python (Join-Path $Root 'run_regime_window_comparison.py') --variant 8y --send
$exitCode = $LASTEXITCODE
Write-Host "[$(Get-Date -Format s)] run_regime_window_comparison.py exit code: $exitCode"

exit $exitCode
