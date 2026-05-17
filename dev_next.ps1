# v64 - start service if not running
$procs = Get-Process python* -ErrorAction SilentlyContinue
if ($procs) {
    Write-Output "Python already running: $($procs.Count) process(es)"
} else {
    Write-Output "No Python running - starting service..."
    Start-Process powershell -ArgumentList "-File C:\ProgramData\LifeLog\Start-LifeLog.ps1" -WindowStyle Normal
    Start-Sleep 5
    $procs2 = Get-Process python* -ErrorAction SilentlyContinue
    if ($procs2) {
        Write-Output "Service started successfully: $($procs2.Count) process(es)"
    } else {
        Write-Output "WARNING: Service still not running after start attempt"
    }
}
