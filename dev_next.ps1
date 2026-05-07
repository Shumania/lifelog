# dev_next.ps1 v29 - re-run extraction to pick up failed chunk 9
$computer = $env:COMPUTERNAME
Write-Host "[$computer] Re-running lifelog_extract.py to pick up any missing chunks..."

$pythonExe = $null

# Find real Python (skip Windows Store stubs)
$candidates = @(
    "C:\Python312\python.exe",
    "C:\Python311\python.exe",
    "C:\Python310\python.exe",
    "C:\Python39\python.exe",
    "C:\Python38\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python39\python.exe",
    "C:\ProgramData\LifeLog\python-embed\python.exe"
)

foreach ($p in $candidates) {
    if (Test-Path $p) {
        $pythonExe = $p
        Write-Host "[$computer] Using Python: $p"
        break
    }
}

if (-not $pythonExe) {
    # Try PATH but skip WindowsApps
    try {
        $found = (Get-Command python -ErrorAction SilentlyContinue).Source
        if ($found -and $found -notlike "*WindowsApps*") {
            $pythonExe = $found
            Write-Host "[$computer] Using Python from PATH: $found"
        }
    } catch {}
}

if (-not $pythonExe) {
    Write-Host "[$computer] ERROR: No real Python found. Please install Python from python.org"
    exit 1
}

$scriptPath = "C:\ProgramData\LifeLog\lifelog_extract.py"
if (-not (Test-Path $scriptPath)) {
    Write-Host "[$computer] ERROR: $scriptPath not found. Please run Update-LifeLog.ps1 first."
    exit 1
}

Write-Host "[$computer] Running extraction..."
& $pythonExe $scriptPath 2>&1
Write-Host "[$computer] Done."
