# Check if service is running, show version, and restart it
$proc = Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*lifelog_service*' }
if ($proc) {
    Write-Output "Service PID: $($proc.Id) running"
    Write-Output "Stopping old service..."
    $proc | Stop-Process -Force
    Start-Sleep -Seconds 2
}

# Start the new version
Write-Output "Starting lifelog_service.py v1.45..."
Start-Process -FilePath "python" -ArgumentList "C:\ProgramData\LifeLog\lifelog_service.py" -WorkingDirectory "C:\ProgramData\LifeLog" -WindowStyle Hidden
Start-Sleep -Seconds 3
$newProc = Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*lifelog_service*' }
if ($newProc) {
    Write-Output "Service restarted: PID $($newProc.Id)"
} else {
    Write-Output "WARNING: Service did not start"
}
