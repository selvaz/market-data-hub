# market_data_hub HMM regime monitor -- 8-year window fit (diagnostic/
# comparison fit, not a day-to-day monitor: no report, no Telegram here).
# Requires environment variables:
#   MARKET_DATA_DB
#   LAZYSTATS_RESULT_DEPOT_DB

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

Import-PersistedEnvVar "MARKET_DATA_DB"
Import-PersistedEnvVar "LAZYSTATS_RESULT_DEPOT_DB"

Write-Host "[$(Get-Date -Format s)] Starting 8-year HMM regime fit"
& $Python (Join-Path $Root 'run_regime_daily.py') --lookback-years 8
$exitCode = $LASTEXITCODE
Write-Host "[$(Get-Date -Format s)] run_regime_daily.py --lookback-years 8 exit code: $exitCode"

exit $exitCode
