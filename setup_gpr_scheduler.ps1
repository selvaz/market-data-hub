# Register disabled; enable only after owner approval and deployment.
param(
    [Parameter(Mandatory = $true)][string]$Database,
    [string]$Python = "C:\ProgramData\spyder-6\python.exe",
    [switch]$Run
)
$ErrorActionPreference = "Stop"
$rootPath = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not [IO.Path]::IsPathRooted($Database)) { throw "Database must be an absolute path" }
if ($Run) {
    & $Python (Join-Path $rootPath 'run_gpr.py') --db $Database
    exit $LASTEXITCODE
}
$taskName = 'InvestmentCommittee_MarketData_GPR'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw "Task $taskName already exists; inspect it before replacing it."
}
$scriptPath = $MyInvocation.MyCommand.Path
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Run -Database "{1}" -Python "{2}"' -f $scriptPath, $Database, $Python
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Daily -At '14:10'
$settings = New-ScheduledTaskSettingsSet -Disable -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description 'GPR workbooks; daily retry; initially disabled' | Out-Null
Write-Host "Created disabled task $taskName at 14:10 local time (machine must use Pacific time)."
