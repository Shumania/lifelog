#Requires -RunAsAdministrator
<#
.SYNOPSIS
    LifeLog iPhone Sync Installer

.DESCRIPTION
    - Creates C:\ProgramData\LifeLog\
    - Installs Python 3.12 (if needed)
    - Downloads latest lifelog_extract.py and Update-LifeLog.ps1 from GitHub
    - Installs required Python packages

.NOTES
    Run as Administrator. Safe to re-run on multiple PCs.
#>

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$INSTALL_DIR = "C:\ProgramData\LifeLog"
$LOG_PATH    = "$INSTALL_DIR\install.log"
$SCRIPT_URL  = "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_extract.py"
$UPDATER_URL = "https://raw.githubusercontent.com/Shumania/lifelog/main/Update-LifeLog.ps1"

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    Write-Host $line
    try { Add-Content -Path $LOG_PATH -Value $line -Encoding UTF8 } catch {}
}

function Get-PythonExe {
    # Try 'py' launcher first (most reliable on Windows)
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $ver = & py --version 2>&1
        if ($ver -match "Python 3\.([89]|1[0-9])") { return "py" }
    }
    # Try 'python' but skip Windows Store stub (WindowsApps path can't run pip)
    $sys = Get-Command python -ErrorAction SilentlyContinue
    if ($sys -and $sys.Source -notlike "*WindowsApps*") {
        $ver = & $sys.Source --version 2>&1
        if ($ver -match "Python 3\.([89]|1[0-9])") { return $sys.Source }
    }
    # Try common real install paths
    $candidates = @(
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe",
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

# -- Header --------------------------------------------------------------------
Write-Host ""
Write-Host "+=============================================+"
Write-Host "|       LifeLog iPhone Sync Installer        |"
Write-Host "+=============================================+"
Write-Host ""

# -- Create install directory --------------------------------------------------
New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null
Write-Log "Install directory: $INSTALL_DIR"

# -- Check/Install Python ------------------------------------------------------
Write-Log "Checking for Python 3.8+..."
$pythonExe = Get-PythonExe

if (-not $pythonExe) {
    Write-Log "Python not found. Trying winget install..."
    $winget = Get-Command winget -ErrorAction SilentlyContinue

    if ($winget) {
        try {
            Write-Log "Running: winget install Python.Python.3.12 --silent"
            & winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements 2>&1 | ForEach-Object { Write-Log $_ }
            # Refresh PATH
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
            $pythonExe = Get-PythonExe
            if ($pythonExe) { Write-Log "Python installed via winget: $pythonExe" }
        } catch {
            Write-Log "winget install failed: $_"
        }
    }

    if (-not $pythonExe) {
        Write-Log "ERROR: Python not found and could not be installed automatically."
        Write-Log "Please install Python 3.12 from https://www.python.org/ and re-run this script."
        exit 1
    }
} else {
    Write-Log "Found Python at: $pythonExe"
}

# -- Install Python packages ---------------------------------------------------
Write-Log "Installing required Python packages..."
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$pipOutput = & $pythonExe -m pip install --quiet --upgrade iphone_backup_decrypt 2>&1
$pipExit = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($pipExit -ne 0) {
    Write-Log "WARNING: pip exited with code $pipExit. Output: $pipOutput"
    Write-Log "Continuing anyway - package may already be installed."
} else {
    Write-Log "pip completed successfully."
}

# -- Download latest scripts from GitHub ---------------------------------------
Write-Log "Downloading latest lifelog_extract.py from GitHub..."
Invoke-WebRequest -Uri "$SCRIPT_URL?v=$(Get-Date -Format 'yyyyMMddHHmmss')" -OutFile "$INSTALL_DIR\lifelog_extract.py" -UseBasicParsing
$lines = (Get-Content "$INSTALL_DIR\lifelog_extract.py").Count
Write-Log "lifelog_extract.py downloaded ($lines lines)."

Write-Log "Downloading Update-LifeLog.ps1 from GitHub..."
Invoke-WebRequest -Uri "$UPDATER_URL?v=$(Get-Date -Format 'yyyyMMddHHmmss')" -OutFile "$INSTALL_DIR\Update-LifeLog.ps1" -UseBasicParsing
Write-Log "Update-LifeLog.ps1 downloaded."

# -- Done ----------------------------------------------------------------------
Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "        Installation Complete!                  " -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
Write-Host "To extract your iPhone data, run:" -ForegroundColor Cyan
Write-Host "  python C:\ProgramData\LifeLog\lifelog_extract.py" -ForegroundColor Cyan
Write-Host ""
Write-Host "To update to the latest script version, run:" -ForegroundColor Cyan
Write-Host "  C:\ProgramData\LifeLog\Update-LifeLog.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Log "Installation complete."
