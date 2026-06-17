# ============================================================================
# setup_scheduler.ps1 — creates the Windows scheduled tasks for market_data_hub
#
# Run from PowerShell as administrator:
#     powershell -ExecutionPolicy Bypass -File D:\market_data\setup_scheduler.ps1
#
# To remove the tasks:
#     powershell -ExecutionPolicy Bypass -File D:\market_data\setup_scheduler.ps1 -Remove
# ============================================================================
param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$root   = "D:\market_data"
# Prefer the project virtualenv interpreter (pinned dependencies); fall back to
# the python on PATH only if the venv has not been created yet.
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $python = $venvPython
} else {
    $python = (Get-Command python).Source
    Write-Warning "venv not found at $venvPython — falling back to $python. Create it with: py -m venv $root\.venv; $root\.venv\Scripts\pip install -r requirements.txt"
}
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$tasks = @(
    @{ Name = "MarketDataEOD";     Time = "22:00"; Args = "run_daily.py --report --send-email"; Daily = $true  },
    @{ Name = "MarketDataWeekend"; Time = "08:00"; Args = "run_daily.py --sources fred --report --send-email"; Daily = $false }
)

if ($Remove) {
    foreach ($t in $tasks) {
        if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false
            Write-Host "Removed task $($t.Name)"
        }
    }
    # live (intraday) task created separately below
    if (Get-ScheduledTask -TaskName "MarketDataLive" -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName "MarketDataLive" -Confirm:$false
        Write-Host "Removed task MarketDataLive"
    }
    Write-Host "Done."
    return
}

function New-MdTask($name, $time, $argline, $trigger) {
    $logFile = Join-Path $logDir "$name.log"
    # wrapper: redirects stdout+stderr to a log file
    $cmd = "/c `"cd /d $root && `"$python`" $argline >> `"$logFile`" 2>&1`""
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmd
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
        -Settings $settings -Description "market_data_hub: $argline" | Out-Null
    Write-Host "Created task '$name' ($time) -> $argline"
}

# 1) Daily EOD 22:00 — full download + report + email
New-MdTask "MarketDataEOD" "22:00" "run_daily.py --report --send-email" `
    (New-ScheduledTaskTrigger -Daily -At "22:00")

# 2) Weekend (Saturday 08:00) — FRED refresh + report + email
New-MdTask "MarketDataWeekend" "08:00" "run_daily.py --sources fred --report --send-email" `
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At "08:00")

# 3) Live intraday — every hour from 16:00 to 22:00 (US markets), Mon-Fri only.
# A weekly weekday trigger carries the hourly repetition (a bare -Once trigger
# would also fire on weekends, when markets are closed).
$liveTrigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "16:00"
$liveTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At "16:00" `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Hours 6)).Repetition
New-MdTask "MarketDataLive" "16:00-22:00 Mon-Fri" "run_daily.py --live-only" $liveTrigger

Write-Host ""
Write-Host "Tasks created. Verify with: Get-ScheduledTask -TaskName MarketData*"
Write-Host "Logs in: $logDir"
