# dev_next.ps1 v31 - force-download latest lifelog_extract.py and run extraction
$ErrorActionPreference = "Continue"
Write-Host "[$env:COMPUTERNAME] dev_next v31: downloading latest lifelog_extract.py and running extraction..."

$installDir = "C:\ProgramData\LifeLog"
$scriptPath = "$installDir\lifelog_extract.py"

# Ensure install dir exists
if (-not (Test-Path $installDir)) {
    New-Item -ItemType Directory -Path $installDir -Force | Out-Null
}

# Force-download latest script (cache-bust with timestamp)
$ts = [int][double]::Parse((Get-Date -UFormat %s))
$url = "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?v=$ts"
Write-Host "[$env:COMPUTERNAME] Downloading lifelog_extract.py..."
try {
    Invoke-WebRequest -Uri $url -OutFile $scriptPath -UseBasicParsing
    $lines = (Get-Content $scriptPath).Count
    Write-Host "[$env:COMPUTERNAME] Downloaded OK ($lines lines)"
} catch {
    Write-Host "[$env:COMPUTERNAME] ERROR downloading script: $_"
    exit 1
}

# Find Python (skip Windows Store stub)
$pythonExe = $null
$candidates = @(
    "C:\Python312\python.exe",
    "C:\Python311\python.exe",
    "C:\Python310\python.exe",
    "C:\Python39\python.exe",
    "C:\Python38\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
)
foreach ($p in $candidates) {
    if (Test-Path $p) {
        $ver = & $p --version 2>&1
        Write-Host "[$env:COMPUTERNAME] Found Python: $p ($ver)"
        $pythonExe = $p
        break
    }
}
if (-not $pythonExe) {
    # Try py launcher
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $ver = & py --version 2>&1
        Write-Host "[$env:COMPUTERNAME] Found py launcher: $ver"
        $pythonExe = "py"
    }
}
if (-not $pythonExe) {
    Write-Host "[$env:COMPUTERNAME] ERROR: No Python found. Install Python 3.8+ from python.org"
    exit 1
}

# Install iphone_backup_decrypt if needed
Write-Host "[$env:COMPUTERNAME] Ensuring iphone_backup_decrypt is installed..."
& $pythonExe -m pip install --quiet --upgrade iphone_backup_decrypt 2>&1 | Out-Null

# Run extraction
Write-Host "[$env:COMPUTERNAME] Running extraction (single-request mode)..."
& $pythonExe $scriptPath 2>&1
Write-Host "[$env:COMPUTERNAME] Extraction complete."
