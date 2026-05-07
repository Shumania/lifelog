# dev_next.ps1 v28 - inline setup, no separate installer script
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy Bypass -Force
$ErrorActionPreference = "Continue"

Write-Host "=== LifeLog Setup & Extract ==="
Write-Host "Machine: $env:COMPUTERNAME"

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
        "C:\Python311\python.exe",
        "C:\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
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
    Write-Host "ERROR: No Python 3.8+ found (skipping Windows Store stub)."
    exit 1
}
Write-Host "Python: $pythonExe"
$verOut = & $pythonExe --version 2>&1
Write-Host "Version: $verOut"

# --- Create install dir ---
$installDir = "C:\ProgramData\LifeLog"
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
Write-Host "Install dir: $installDir"

# --- Install iphone_backup_decrypt ---
Write-Host "Installing iphone_backup_decrypt..."
& $pythonExe -m pip install --user -q --disable-pip-version-check iphone_backup_decrypt 2>&1 | Write-Host
Write-Host "pip exit: $LASTEXITCODE"

# --- Download latest lifelog_extract.py ---
$scriptUrl = "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py?v=$(Get-Date -Format 'yyyyMMddHHmmss')"
Write-Host "Downloading lifelog_extract.py..."
Invoke-WebRequest -Uri $scriptUrl -OutFile "$installDir\lifelog_extract.py" -UseBasicParsing
$lineCount = (Get-Content "$installDir\lifelog_extract.py").Count
Write-Host "Downloaded: $lineCount lines"

# --- Run extraction ---
Write-Host "Running extraction..."
& $pythonExe "$installDir\lifelog_extract.py"
Write-Host "Extraction exit: $LASTEXITCODE"
