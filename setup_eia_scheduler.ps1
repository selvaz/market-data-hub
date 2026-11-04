# Registers a disabled daily 14:00 Pacific task; explicitly enable only after
# deploying a reviewed release and configuring the production database.
param(
    [Parameter(Mandatory = $true)][string]$Database,
    [string]$Python = "C:\ProgramData\spyder-6\python.exe",
    [switch]$Run
)
$ErrorActionPreference = "Stop"
$rootPath = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not [IO.Path]::IsPathRooted($Database)) { throw "Database must be an absolute path" }
if ($Run) {
    $env:EIA_API_KEY = [Environment]::GetEnvironmentVariable('EIA_API_KEY', 'User')
    & $Python (Join-Path $rootPath 'run_eia_inventories.py') --db $Database
    exit $LASTEXITCODE
}
$taskName = 'InvestmentCommittee_MarketData_EIAInventories'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw "Task $taskName already exists; inspect it before replacing it."
}
$scriptPath = $MyInvocation.MyCommand.Path
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Run -Database "{1}" -Python "{2}"' -f $scriptPath, $Database, $Python
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Daily -At '14:00'
$settings = New-ScheduledTaskSettingsSet -Disable -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description 'EIA weekly inventories; daily retry after normal release times; initially disabled' | Out-Null
Write-Host "Created disabled task $taskName at 14:00 local time (machine must use Pacific time)."
