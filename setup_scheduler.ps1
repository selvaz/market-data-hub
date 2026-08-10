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
$regimeWrapper = Join-Path $root "run_regime_daily_with_telegram.ps1"
$regime8yWrapper = Join-Path $root "run_regime_8y.ps1"
$regimeComparisonWrapper = Join-Path $root "run_regime_window_comparison_with_telegram.ps1"
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if ($Remove) {
    foreach ($name in @("MarketData_EU18", "MarketData_USClose", "MarketDataEOD", "MarketDataWeekend", "MarketDataLive", "MarketData_HMMRegime", "MarketData_HMMRegime_8Y", "MarketData_RegimeWindowComparison")) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "Removed task $name"
        }
    }
    Write-Host "Done."
    return
}

function New-MdTask($name, $time, $runDailyArgs, $trigger, $wrapperPath = $wrapper, $argName = "RunDailyArgs") {
    $logFile = Join-Path $logDir "$name.log"
    # -Command (not -File) so PowerShell's own parser sees *>>: Task Scheduler
    # invokes powershell.exe directly (no cmd.exe), and -File passes ">>"/"2>&1"
    # through as inert literal arguments instead of redirecting output — the
    # wrapper would run but logs/*.log would silently stay empty.
    $argText = ($runDailyArgs | ForEach-Object { "'" + $_.Replace("'", "''") + "'" }) -join ","
    $cmdString = "& '$wrapperPath' -$argName $argText *>> '$logFile'"
    $psArgs = "-NoProfile -ExecutionPolicy Bypass -Command `"$cmdString`""
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $psArgs
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 4)
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
        -Settings $settings -Description "market_data_hub: daily refresh + Telegram report" | Out-Null
    Write-Host "Created task '$name' ($time) -> $(Split-Path -Leaf $wrapperPath) $($runDailyArgs -join ' ')"
}

function New-MdTaskNoArgs($name, $time, $wrapperPath, $trigger) {
    # Like New-MdTask but for wrappers that take no parameters at all
    # (run_regime_8y.ps1 / run_regime_window_comparison_with_telegram.ps1 --
    # their args are hardcoded inside the wrapper, not passed through).
    $logFile = Join-Path $logDir "$name.log"
    $cmdString = "& '$wrapperPath' *>> '$logFile'"
    $psArgs = "-NoProfile -ExecutionPolicy Bypass -Command `"$cmdString`""
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $psArgs
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 4)
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
        -Settings $settings -Description "market_data_hub: $name" | Out-Null
    Write-Host "Created task '$name' ($time) -> $(Split-Path -Leaf $wrapperPath)"
}

# Machine time zone is expected to be Pacific on this workstation:
# 09:00 Pacific ~= 18:00 Europe/Rome during normal US/EU DST overlap.
New-MdTask "MarketData_EU18" "09:00 daily" @("--report") `
    (New-ScheduledTaskTrigger -Daily -At "09:00")

# 13:15 Pacific is ~15 minutes after the 16:00 New York cash close.
New-MdTask "MarketData_USClose" "13:15 Mon-Fri" @("--report") `
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "13:15")

# 13:45 Pacific: 30 minutes after MarketData_USClose, once that day's prices have
# landed. Runs as its own independent scheduled task (separate from the download
# pipeline), via its own wrapper script.
New-MdTask "MarketData_HMMRegime" "13:45 Mon-Fri" @("--send") `
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "13:45") `
    $regimeWrapper "RunRegimeArgs"

# MarketData_HMMRegime's own runtime has grown from ~30-40 min (mid-July) to
# 79-84 min (08-03/08-04) as its return history lengthens -- it starts at
# 13:45 and finishes as late as 15:09. 8Y used to run at 14:35 (before that
# growth) and silently missed the DB write lock two days running:
# run_regime_daily.py --lookback-years 8 catches DBLockTimeout and exits 0
# ("SKIP: Another writer holds the DB lock"), so the scheduled task showed
# success while doing nothing. 15:45 gives a ~35 min buffer past the worst
# observed finish, with room for HMMRegime to keep growing.
New-MdTaskNoArgs "MarketData_HMMRegime_8Y" "15:45 Mon-Fri" $regime8yWrapper `
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "15:45")

# Must run after MarketData_HMMRegime_8Y actually finishes (it reads the 8y
# depot series that job just wrote) -- moved in lockstep with it, same reasoning.
New-MdTaskNoArgs "MarketData_RegimeWindowComparison" "17:00 Mon-Fri" $regimeComparisonWrapper `
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "17:00")

Write-Host ""
Write-Host "Tasks created. Verify with: Get-ScheduledTask -TaskName MarketData*"
Write-Host "Logs in: $logDir"

