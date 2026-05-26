# Kill stuck service and restart v1.44 with output capture
$installDir = 'C:\ProgramData\LifeLog'
$serviceFile = Join-Path $installDir 'lifelog_service.py'
$logFile = Join-Path $installDir 'startup_debug.log'
$errLog = Join-Path $installDir 'startup_error.log'

# Aggressively kill ALL python processes that have lifelog_service in their command line
# Use WMI which reliably reads CommandLine unlike Get-Process
$wmiProcs = Get-WmiObject Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue
$killed = 0
foreach ($wp in $wmiProcs) {
    if ($wp.CommandLine -match 'lifelog_service') {
        Write-Output "Killing PID=$($wp.ProcessId): $($wp.CommandLine)"
        Stop-Process -Id $wp.ProcessId -Force -ErrorAction SilentlyContinue
        $killed++
    }
}
if ($killed -eq 0) {
    Write-Output "No lifelog_service processes found via WMI"
    # Fallback: kill ALL python processes (nuclear option)
    $allPy = Get-Process python*, python3* -ErrorAction SilentlyContinue
    if ($allPy) {
        Write-Output "Fallback: killing $($allPy.Count) python process(es)"
        $allPy | Stop-Process -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Output "Killed $killed process(es)"
}
Start-Sleep -Seconds 5

# Double-check nothing remains
$remaining = Get-WmiObject Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'lifelog_service' }
if ($remaining) {
    Write-Output "WARNING: $($remaining.Count) process(es) STILL alive after kill!"
    foreach ($r in $remaining) {
        Write-Output "  PID=$($r.ProcessId) — force taskkill"
        & taskkill /F /PID $r.ProcessId 2>&1
    }
    Start-Sleep -Seconds 3
}

# Clean rollback artifacts
$flagFiles = @('update_in_progress', 'update_started', 'lifelog_service.py.bak')
foreach ($f in $flagFiles) {
    $fp = Join-Path $installDir $f
    if (Test-Path $fp) {
        Remove-Item $fp -Force
        Write-Output "Cleaned: $f"
    }
}

# Start fresh — the mutex is released once the old process is dead
Write-Output "Starting v1.44..."
$p = Start-Process -FilePath 'python' -ArgumentList $serviceFile `
    -WorkingDirectory $installDir `
    -RedirectStandardOutput $logFile `
    -RedirectStandardError $errLog `
    -PassThru -NoNewWindow:$false

Start-Sleep -Seconds 15

if ($p.HasExited) {
    Write-Output "EXITED code $($p.ExitCode)"
    Write-Output '=== STDOUT (last 30 lines) ==='
    if (Test-Path $logFile) {
        Get-Content $logFile -Tail 30 | ForEach-Object { Write-Output $_ }
    }
    Write-Output '=== STDERR ==='
    if (Test-Path $errLog) {
        Get-Content $errLog -Tail 30 | ForEach-Object { Write-Output $_ }
    }
} else {
    Write-Output "Running PID=$($p.Id)"
    if (Test-Path $logFile) {
        Write-Output '=== First 20 lines ==='
        Get-Content $logFile -Head 20 | ForEach-Object { Write-Output $_ }
    }
}
