# Diagnose SSE and update state
$dir = 'C:\ProgramData\LifeLog'
Write-Output "=== SKIP VERSION ==="
if (Test-Path "$dir\skip_version") { Get-Content "$dir\skip_version" } else { Write-Output "(not set)" }
Write-Output ""
Write-Output "=== SERVICE VERSION IN FILE ==="
$svc = Get-Content "$dir\lifelog_service.py" -Raw
if ($svc -match 'SERVICE_VERSION\s*=\s*"([^"]+)"') { Write-Output $matches[1] } else { Write-Output "(not found)" }
Write-Output ""
Write-Output "=== VERSIONS.JSON ON DISK ==="
if (Test-Path "$dir\versions.json") { Get-Content "$dir\versions.json" } else { Write-Output "(not found)" }
Write-Output ""
Write-Output "=== PROCESS CHECK ==="
Get-Process -Name python* -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, StartTime | Format-Table
Write-Output ""
Write-Output "=== LAST 5 LINES OF LIFELOG SERVICE BACKUP ==="
if (Test-Path "$dir\lifelog_service.py.bak") { Get-Content "$dir\lifelog_service.py.bak" -TotalCount 5 } else { Write-Output "(no .bak file)" }
