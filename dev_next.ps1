# dev_next.ps1 v29 — re-run podcast extraction
$computer = $env:COMPUTERNAME
Write-Host "[$computer] Running lifelog_extract.py to re-send podcast data..."

$python = $null
$candidates = @(
    "C:\ProgramData\LifeLog\python\python.exe",
    "C:\Python312\python.exe",
    "C:\Python311\python.exe",
    "C:\Python310\python.exe",
    "C:\Python39\python.exe"
)
foreach ($p in $candidates) {
    if (Test-Path $p) { $python = $p; break }
}
if (-not $python) {
    # Try py launcher
    try {
        $v = & py --version 2>&1
        if ($v -match "Python 3\.[89]|3\.1[0-9]") { $python = "py" }
    } catch {}
}
if (-not $python) {
    # Last resort: python from PATH, but skip WindowsApps stub
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found -and $found.Source -notmatch "WindowsApps") {
        $python = $found.Source
    }
}
if (-not $python) {
    Write-Host "ERROR: Python not found"
    exit 1
}

Write-Host "[$computer] Using Python: $python"

$script = "C:\ProgramData\LifeLog\lifelog_extract.py"
if (-not (Test-Path $script)) {
    Write-Host "[$computer] Downloading lifelog_extract.py..."
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?v=29" -OutFile $script
}

& $python $script
Write-Host "[$computer] Done."
