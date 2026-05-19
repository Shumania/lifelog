# v70 - Investigate v1.23 crash: check Event Log + rollback artifacts
Write-Output "=== ROLLBACK FILES ==="
$lifelogDir = "C:\ProgramData\LifeLog"
@("lifelog_service.py.bak", "update_in_progress", "update_started") | ForEach-Object {
    $p = Join-Path $lifelogDir $_
    if (Test-Path $p) {
        $fi = Get-Item $p
        Write-Output "  FOUND: $_ (size=$($fi.Length), modified=$($fi.LastWriteTime))"
    } else {
        Write-Output "  NOT FOUND: $_"
    }
}

Write-Output ""
Write-Output "=== WINDOWS EVENT LOG: Application Errors (last 24h, python) ==="
try {
    $cutoff = (Get-Date).AddHours(-24)
    $events = Get-WinEvent -FilterHashtable @{LogName='Application'; Level=2; StartTime=$cutoff} -ErrorAction SilentlyContinue |
        Where-Object { $_.Message -match 'python' -or $_.ProviderName -match 'Application Error' } |
        Select-Object -First 10
    if ($events) {
        $events | ForEach-Object {
            Write-Output "[$($_.TimeCreated)] $($_.ProviderName): $($_.Message.Substring(0, [Math]::Min(500, $_.Message.Length)))"
        }
    } else {
        Write-Output "  No python-related application errors in last 24h"
    }
} catch {
    Write-Output "  Error reading event log: $_"
}

Write-Output ""
Write-Output "=== FULL LOG AROUND CRASH TIME (21:02 UTC = 2:02 PM Seattle) ==="
$logPath = Join-Path $lifelogDir "lifelog_service.log"
if (Test-Path $logPath) {
    $lines = Get-Content $logPath
    # Find lines around the v1.23 update
    $crashWindow = $lines | Where-Object { $_ -match "2026-05-18T2[01]:" -or $_ -match "2026-05-19T0[0-2]:" }
    if ($crashWindow) {
        $crashWindow | ForEach-Object { Write-Output $_ }
    } else {
        Write-Output "  No log lines in the crash time window"
    }
} else {
    Write-Output "  Log file not found"
}

Write-Output ""
Write-Output "=== PROCESS START HISTORY (today) ==="
Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4688; StartTime=(Get-Date).Date} -ErrorAction SilentlyContinue |
    Where-Object { $_.Message -match 'python' } |
    Select-Object -First 10 |
    ForEach-Object { Write-Output "[$($_.TimeCreated)] Process created: python" }
if (-not $?) { Write-Output "  Security audit log not available or no python process events" }
