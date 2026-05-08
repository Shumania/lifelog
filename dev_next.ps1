# dev_next.ps1 v29 - wait for backup to settle, then run extraction
$computer = $env:COMPUTERNAME

# Find backup folder
$backupRoot = "$env:USERPROFILE\Apple\MobileSync\Backup"
if (-not (Test-Path $backupRoot)) {
    $backupRoot = "$env:USERPROFILE\AppData\Roaming\Apple Computer\MobileSync\Backup"
}

$backupFolder = Get-ChildItem $backupRoot -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1

if (-not $backupFolder) {
    Write-Output "[$computer] ERROR: No backup folder found under $backupRoot"
    exit 1
}

Write-Output "[$computer] Backup folder: $($backupFolder.FullName)"
Write-Output "[$computer] Last modified: $($backupFolder.LastWriteTime)"

# Wait until backup folder hasn't changed for 30 seconds
$maxWait = 300  # 5 minutes max
$idle = 0
$interval = 10
$lastWrite = $backupFolder.LastWriteTime

Write-Output "[$computer] Waiting for backup to finish..."
while ($idle -lt 30 -and $maxWait -gt 0) {
    Start-Sleep -Seconds $interval
    $maxWait -= $interval
    $current = (Get-Item $backupFolder.FullName).LastWriteTime
    if ($current -eq $lastWrite) {
        $idle += $interval
        Write-Output "[$computer] Backup idle for ${idle}s..."
    } else {
        $idle = 0
        $lastWrite = $current
        Write-Output "[$computer] Backup still writing..."
    }
}

if ($maxWait -le 0) {
    Write-Output "[$computer] WARNING: Timed out waiting for backup. Running extraction anyway."
}

Write-Output "[$computer] Backup settled. Running extraction..."

$python = $null
$candidates = @(
    "C:\Python312\python.exe","C:\Python311\python.exe","C:\Python310\python.exe",
    "C:\Python39\python.exe","C:\Python38\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
)
foreach ($c in $candidates) {
    if (Test-Path $c) { $python = $c; break }
}
if (-not $python) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found -and $found.Source -notlike "*WindowsApps*") { $python = $found.Source }
}
if (-not $python) {
    Write-Output "[$computer] ERROR: No valid Python found"
    exit 1
}

Write-Output "[$computer] Using Python: $python"

$script = "C:\ProgramData\LifeLog\lifelog_extract.py"
if (-not (Test-Path $script)) {
    Write-Output "[$computer] Downloading lifelog_extract.py..."
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?v=3" -OutFile $script
}

& $python $script 2>&1
