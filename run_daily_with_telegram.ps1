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

Write-Host "[$(Get-Date -Format s)] Starting market_data_hub daily refresh: $($RunDailyArgs -join ' ')"
& $Python (Join-Path $Root 'run_daily.py') @RunDailyArgs
$dailyExit = $LASTEXITCODE
Write-Host "[$(Get-Date -Format s)] run_daily.py exit code: $dailyExit"

Write-Host "[$(Get-Date -Format s)] Sending Telegram run report"
& $Python (Join-Path $Root 'send_telegram_run_report.py') --save
$telegramExit = $LASTEXITCODE
Write-Host "[$(Get-Date -Format s)] Telegram report exit code: $telegramExit"

exit $dailyExit
