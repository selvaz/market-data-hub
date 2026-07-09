# market_data_hub HMM regime monitor + Telegram report wrapper
# Requires environment variables:
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID

param(
    [string[]]$RunRegimeArgs = @('--send')
)

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
Import-PersistedEnvVar "MARKET_DATA_REPORT_DIR"
Import-PersistedEnvVar "TELEGRAM_BOT_TOKEN"
Import-PersistedEnvVar "TELEGRAM_CHAT_ID"

Write-Host "[$(Get-Date -Format s)] Starting HMM regime monitor: $($RunRegimeArgs -join ' ')"
& $Python (Join-Path $Root 'run_regime_daily.py') @RunRegimeArgs
$exitCode = $LASTEXITCODE
Write-Host "[$(Get-Date -Format s)] run_regime_daily.py exit code: $exitCode"

exit $exitCode
