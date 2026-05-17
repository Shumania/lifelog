# v67 - kill old service + restart from updated file on disk
Write-Output "=== KILLING OLD lifelog_service.py PROCESSES ==="
$procs = Get-Process python* -ErrorAction SilentlyContinue | Where-Object {
    (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine -like "*lifelog_service*"
}
if ($procs) {
    $procs | ForEach-Object { 
        Write-Output "Killing PID $($_.Id)"
        Stop-Process -Id $_.Id -Force
    }
} else {
    Write-Output "No lifelog_service.py processes found"
}

Start-Sleep -Seconds 2

Write-Output "=== STARTING lifelog_service.py ==="
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python3 -ErrorAction SilentlyContinue).Source }
Write-Output "Python: $py"
Write-Output "Starting service..."
Start-Process $py -ArgumentList "C:\ProgramData\LifeLog\lifelog_service.py" -WindowStyle Normal
Start-Sleep -Seconds 3

Write-Output "=== PROCESS CHECK ==="
Get-Process python* -ErrorAction SilentlyContinue | ForEach-Object {
    $cmd = (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
    Write-Output "PID $($_.Id): $cmd"
}
Write-Output "Done."
