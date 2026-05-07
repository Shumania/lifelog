# dev_next.ps1 v29 — run extraction (setup already done)
$ErrorActionPreference = "Continue"

$installDir = "C:\ProgramData\LifeLog"
$scriptUrl  = "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?v=29"
$scriptPath = "$installDir\lifelog_extract.py"

# Download latest extract script
Write-Host "Downloading latest lifelog_extract.py..."
try {
    Invoke-WebRequest -Uri $scriptUrl -OutFile $scriptPath -UseBasicParsing
    $lines = (Get-Content $scriptPath).Count
    Write-Host "Downloaded OK ($lines lines)."
} catch {
    Write-Host "ERROR downloading script: $_"
    exit 1
}

# Find python (skip Windows Store stub)
$pythonExe = $null
foreach ($candidate in @("python3", "python")) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found) {
        $path = $found.Source
        if ($path -notlike "*WindowsApps*") {
            $pythonExe = $path
            break
        }
    }
}
if (-not $pythonExe) {
    # Try known install paths
    foreach ($p in @("C:\Python312\python.exe","C:\Python311\python.exe","C:\Python310\python.exe","C:\Python39\python.exe")) {
        if (Test-Path $p) { $pythonExe = $p; break }
    }
}
if (-not $pythonExe) {
    Write-Host "ERROR: Python not found."
    exit 1
}
Write-Host "Using Python: $pythonExe"

# Run extraction
Write-Host "Running extraction..."
& $pythonExe $scriptPath
