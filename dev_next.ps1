# dev_next.ps1 v29 - just run extraction (setup already done)
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy Bypass -Force
$ErrorActionPreference = "Continue"

Write-Host "=== LifeLog Extract === Machine: $env:COMPUTERNAME"

$installDir = "C:\ProgramData\LifeLog"
New-Item -ItemType Directory -Force -Path $installDir | Out-Null

# --- Find real Python (skip Windows Store stub) ---
function Get-PythonExe {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $ver = & py --version 2>&1
        if ($ver -match "Python 3\.([89]|1[0-9])") { return "py" }
    }
    $sys = Get-Command python -ErrorAction SilentlyContinue
    if ($sys -and $sys.Source -notlike "*WindowsApps*") {
        $ver = & $sys.Source --version 2>&1
        if ($ver -match "Python 3\.([89]|1[0-9])") { return $sys.Source }
    }
    $candidates = @(
        "C:\Python314\python.exe",
        "C:\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe",
        "$env:LOCALAPPDATA\Python\pythoncore-3.12-64\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $ver = & $c --version 2>&1
            if ($ver -match "Python 3\.([89]|1[0-9])") { return $c }
        }
    }
    return $null
}

$pythonExe = Get-PythonExe
if (-not $pythonExe) {
    Write-Host "ERROR: No Python 3.8+ found."
    exit 1
}
$verOut = & $pythonExe --version 2>&1
Write-Host "Python: $pythonExe ($verOut)"

# --- Ensure iphone_backup_decrypt is installed ---
$pipOut = & $pythonExe -m pip show iphone_backup_decrypt 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing iphone_backup_decrypt..."
    & $pythonExe -m pip install --user -q iphone_backup_decrypt 2>&1 | Write-Host
}

# --- Download latest lifelog_extract.py ---
$ts = Get-Date -Format 'yyyyMMddHHmmss'
$scriptUrl = "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?v=$ts"
Invoke-WebRequest -Uri $scriptUrl -OutFile "$installDir\lifelog_extract.py" -UseBasicParsing
$lineCount = (Get-Content "$installDir\lifelog_extract.py").Count
Write-Host "Script downloaded: $lineCount lines"

# --- Run extraction ---
Write-Host "Running extraction..."
& $pythonExe "$installDir\lifelog_extract.py"
Write-Host "Done. Exit: $LASTEXITCODE"
