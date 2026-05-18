# v69 - Check if service is running and get recent log
Write-Output "=== PROCESS CHECK ==="
Get-Process python* 2>$null | Select-Object Id, ProcessName, StartTime | Format-Table -AutoSize
Write-Output "`n=== RECENT LOG (last 50 lines) ==="
if (Test-Path "C:\ProgramData\LifeLog\lifelog_service.log") {
    Get-Content "C:\ProgramData\LifeLog\lifelog_service.log" -Tail 50
} else {
    Write-Output "No log file found"
}
Write-Output "`n=== SERVICE VERSION ==="
if (Test-Path "C:\ProgramData\LifeLog\lifelog_service.py") {
    Select-String 'SERVICE_VERSION' "C:\ProgramData\LifeLog\lifelog_service.py" | Select-Object -First 1
}
