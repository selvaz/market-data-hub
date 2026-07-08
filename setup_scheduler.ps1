# ============================================================================
# setup_scheduler.ps1 — creates the Windows scheduled tasks for market_data_hub
#
# Run from PowerShell as administrator:
#     powershell -ExecutionPolicy Bypass -File .\setup_scheduler.ps1
#
# To remove the tasks:
#     powershell -ExecutionPolicy Bypass -File .\setup_scheduler.ps1 -Remove
# ============================================================================
param(
    [switch]$Remove,
    [string]$Root = "",
    [string]$Python = "C:\ProgramData\spyder-6\python.exe"
)

$ErrorActionPreference = "Stop"
if (!$Root) {
    $Root = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$root = $Root
# Use the Spyder Python environment that is already configured for this workstation.
$python = $Python
$wrapper = Join-Path $root "run_daily_with_telegram.ps1"
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$tasks = @(
    @{ Name = "MarketData_EU18";    Time = "09:00"; Args = @("--report") },
    @{ Name = "MarketData_USClose"; Time = "13:15"; Args = @("--report") }
)

if ($Remove) {
    foreach ($name in @("MarketData_EU18", "MarketData_USClose", "MarketDataEOD", "MarketDataWeekend", "MarketDataLive")) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "Removed task $name"
        }
    }
    Write-Host "Done."
    return
}

function New-MdTask($name, $time, $runDailyArgs, $trigger) {
    $logFile = Join-Path $logDir "$name.log"
    $argText = ($runDailyArgs | ForEach-Object { '"' + $_ + '"' }) -join ","
    $psArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$wrapper`" -RunDailyArgs $argText >> `"$logFile`" 2>&1"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $psArgs
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 4)
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
        -Settings $settings -Description "market_data_hub: daily refresh + Telegram report" | Out-Null
    Write-Host "Created task '$name' ($time) -> run_daily_with_telegram.ps1 $($runDailyArgs -join ' ')"
}

# Machine time zone is expected to be Pacific on this workstation:
# 09:00 Pacific ~= 18:00 Europe/Rome during normal US/EU DST overlap.
New-MdTask "MarketData_EU18" "09:00 daily" @("--report") `
    (New-ScheduledTaskTrigger -Daily -At "09:00")

# 13:15 Pacific is ~15 minutes after the 16:00 New York cash close.
New-MdTask "MarketData_USClose" "13:15 Mon-Fri" @("--report") `
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "13:15")

Write-Host ""
Write-Host "Tasks created. Verify with: Get-ScheduledTask -TaskName MarketData*"
Write-Host "Logs in: $logDir"

