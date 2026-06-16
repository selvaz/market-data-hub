# ============================================================================
# setup_scheduler.ps1 — crea i task pianificati di Windows per market_data_hub
#
# Esegui da PowerShell come amministratore:
#     powershell -ExecutionPolicy Bypass -File D:\market_data\setup_scheduler.ps1
#
# Per rimuovere i task:
#     powershell -ExecutionPolicy Bypass -File D:\market_data\setup_scheduler.ps1 -Remove
# ============================================================================
param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$root   = "D:\market_data"
$python = (Get-Command python).Source
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
            Write-Host "Rimosso task $($t.Name)"
        }
    }
    # task live (intraday) creato separatamente sotto
    if (Get-ScheduledTask -TaskName "MarketDataLive" -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName "MarketDataLive" -Confirm:$false
        Write-Host "Rimosso task MarketDataLive"
    }
    Write-Host "Fatto."
    return
}

function New-MdTask($name, $time, $argline, $trigger) {
    $logFile = Join-Path $logDir "$name.log"
    # wrapper: redirige stdout+stderr su file di log
    $cmd = "/c `"cd /d $root && `"$python`" $argline >> `"$logFile`" 2>&1`""
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmd
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
        -Settings $settings -Description "market_data_hub: $argline" | Out-Null
    Write-Host "Creato task '$name' ($time) -> $argline"
}

# 1) EOD giornaliero 22:00 — full download + report + email
New-MdTask "MarketDataEOD" "22:00" "run_daily.py --report --send-email" `
    (New-ScheduledTaskTrigger -Daily -At "22:00")

# 2) Weekend (sabato 08:00) — refresh FRED + report + email
New-MdTask "MarketDataWeekend" "08:00" "run_daily.py --sources fred --report --send-email" `
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At "08:00")

# 3) Live intraday — ogni ora dalle 16:00 alle 22:00 (mercati US), lun-ven
$liveTrigger = New-ScheduledTaskTrigger -Once -At "16:00" `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Hours 6)
New-MdTask "MarketDataLive" "16:00-22:00" "run_daily.py --live-only" $liveTrigger

Write-Host ""
Write-Host "Task creati. Verifica con: Get-ScheduledTask -TaskName MarketData*"
Write-Host "Log in: $logDir"
