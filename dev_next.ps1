# Force service restart to pick up v2.21 (one-time)
Write-Host "Restarting service for v2.21 update..."
$parentPid = (Get-CimInstance Win32_Process -Filter "ProcessId = $PID").ParentProcessId
Stop-Process -Id $parentPid -Force
