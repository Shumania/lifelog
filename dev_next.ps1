# v62 - service debug: version on disk, process status, last lines of service
$dir = "C:\ProgramData\LifeLog"
$svc = "$dir\lifelog_service.py"

"=== SERVICE VERSION ON DISK ==="
if (Test-Path $svc) {
    Select-String "SERVICE_VERSION\s*=" $svc | Select-Object -First 3
} else {
    "lifelog_service.py NOT FOUND"
}

"=== RUNNING PYTHON PROCESSES ==="
Get-Process python* -ErrorAction SilentlyContinue | Select-Object Id, CPU, MainWindowTitle, StartTime | Format-Table -AutoSize | Out-String

"=== LAST 20 LINES OF LOG ==="
Get-Content "$dir\lifelog.log" -Tail 20 -ErrorAction SilentlyContinue

"=== VERSIONS.JSON ON DISK ==="
Get-Content "$dir\versions.json" -ErrorAction SilentlyContinue

"=== STATE FILES ==="
foreach ($f in @("last_backup_mtime.txt","last_backup_hash.txt","last_podcast_cursor.txt","lifelog_config.json")) {
    $p = "$dir\$f"
    if (Test-Path $p) { "$f = $(Get-Content $p -Raw)" } else { "$f = MISSING" }
}
