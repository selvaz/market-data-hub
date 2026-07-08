# market_data_hub daily refresh + Telegram report wrapper
# Requires environment variables:
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID

param(
    [string[]]$RunDailyArgs = @('--report')
)

$ErrorActionPreference = 'Continue'
$Root = 'C:\Users\Administrator\Documents\GitHub\market-data-hub'
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
Import-PersistedEnvVar "FRED_API_KEY"
Import-PersistedEnvVar "TELEGRAM_BOT_TOKEN"
Import-PersistedEnvVar "TELEGRAM_CHAT_ID"

if (!(Test-Path Env:FRED_API_KEY)) {
    Write-Warning "FRED_API_KEY is not set in process/User/Machine environment; FRED will use the public CSV endpoint."
}

Write-Host "[$(Get-Date -Format s)] Starting market_data_hub daily refresh: $($RunDailyArgs -join ' ')"
& $Python (Join-Path $Root 'run_daily.py') @RunDailyArgs
$dailyExit = $LASTEXITCODE
Write-Host "[$(Get-Date -Format s)] run_daily.py exit code: $dailyExit"

Write-Host "[$(Get-Date -Format s)] Sending Telegram run report"
& $Python (Join-Path $Root 'send_telegram_run_report.py') --save
$telegramExit = $LASTEXITCODE
Write-Host "[$(Get-Date -Format s)] Telegram report exit code: $telegramExit"

exit $dailyExit
