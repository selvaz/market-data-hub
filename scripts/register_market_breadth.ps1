param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Database,
    [string]$TaskName = 'MarketData_MarketBreadth'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $root 'run_market_breadth.py'
$action = New-ScheduledTaskAction -Execute $Python -Argument ('"{0}" --db "{1}"' -f $runner, $Database) -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At '13:50'
$settings = New-ScheduledTaskSettingsSet -Disable
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings
