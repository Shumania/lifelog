# v67 - kill old service, start fresh
Write-Output "=== KILLING OLD SERVICE PROCESSES ==="
$procs = Get-Process python* -ErrorAction SilentlyContinue | Where-Object {
    try { $_.MainWindowTitle -match "lifelog|LifeLog" -or (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -match "lifelog_service" } catch { $false }
}
if ($procs) {
    $procs | ForEach-Object { Write-Output "Killing PID $($_.Id): $($_.MainWindowTitle)"; Stop-Process -Id $_.Id -Force }
} else {
    # Kill all python processes as fallback
    Write-Output "No specific match - killing all python processes"
    Get-Process python* -ErrorAction SilentlyContinue | ForEach-Object { Write-Output "Killing PID $($_.Id)"; Stop-Process -Id $_.Id -Force }
}
Start-Sleep -Seconds 2

Write-Output "=== STARTING FRESH SERVICE ==="
$svcPath = "C:\ProgramData\LifeLog\lifelog_service.py"
if (Test-Path $svcPath) {
    $ver = Select-String "SERVICE_VERSION" $svcPath | Select-Object -First 1
    Write-Output "Service file version line: $ver"
    Start-Process "python" -ArgumentList $svcPath -WindowStyle Normal
    Write-Output "Service started."
} else {
    Write-Output "ERROR: $svcPath not found!"
}
