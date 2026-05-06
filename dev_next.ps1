# v24: Run podcast extraction + Google Maps inspection
# Find Python - use where.exe, skip WindowsApps stub
$pythonExe = $null
$candidates = @()
try { $candidates = @(where.exe python 2>$null) } catch {}
foreach ($p in $candidates) {
    if ($p -notmatch "WindowsApps") {
        $pythonExe = $p
        break
    }
}
if (-not $pythonExe) { $pythonExe = "python" }

Write-Host "v24 | Python: $pythonExe | Machine: $env:COMPUTERNAME"

$ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

# Download latest scripts
$extractUrl  = "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?t=$ts"
$inspectUrl  = "https://raw.githubusercontent.com/Shumania/lifelog/main/inspect_googlemaps_backup.py?t=$ts"
$extractPath = "$env:TEMP\lifelog_extract.py"
$inspectPath = "$env:TEMP\inspect_googlemaps_backup.py"

Invoke-WebRequest -Uri $extractUrl -OutFile $extractPath -UseBasicParsing
Invoke-WebRequest -Uri $inspectUrl -OutFile $inspectPath -UseBasicParsing

Write-Host "Downloaded scripts. Running podcast extraction..."
& $pythonExe $extractPath 2>&1

Write-Host ""
Write-Host "=== GOOGLE MAPS INSPECTION ==="
& $pythonExe $inspectPath 2>&1
