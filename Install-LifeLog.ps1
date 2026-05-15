# Install-LifeLog.ps1
# LifeLog installer — uses GitHub API for all downloads (no raw CDN caching issues)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

$INSTALL_DIR = "C:\ProgramData\LifeLog"
$LOG_PATH    = "$INSTALL_DIR\install.log"
$GITHUB_API  = "https://api.github.com/repos/Shumania/lifelog/contents"
$API_HEADERS = @{ "Accept" = "application/vnd.github.v3+json"; "User-Agent" = "LifeLog-Installer" }

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    Write-Host $line
    try { Add-Content -Path $LOG_PATH -Value $line -Encoding UTF8 } catch {}
}

function Get-GitHubFile {
    param([string]$FileName, [string]$OutPath)
    Write-Log "Downloading $FileName from GitHub..."
    $r = Invoke-RestMethod -Uri "$GITHUB_API/$FileName" -Headers $API_HEADERS
    $content = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(($r.content -replace "`n","")))
    [System.IO.File]::WriteAllText($OutPath, $content, [System.Text.Encoding]::UTF8)
    $lines = ($content -split "`n").Count
    Write-Log "$FileName downloaded ($lines lines)."
}

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
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
            $pythonExe = Get-PythonExe
            if ($pythonExe) { Write-Log "Python installed via winget: $pythonExe" }
        } catch {
            Write-Log "winget install failed: $_"
        }
    }
    if (-not $pythonExe) {
        Write-Log "ERROR: Python not found. Install Python 3.12 from https://www.python.org/ and re-run."
        exit 1
    }
} else {
    Write-Log "Found Python at: $pythonExe"
}

# -- Install Python packages ---------------------------------------------------
Write-Log "Installing required Python packages..."
$ErrorActionPreference = "Continue"
$pipOutput = & $pythonExe -m pip install -q -q --disable-pip-version-check --no-warn-script-location --upgrade iphone_backup_decrypt *>&1 | Out-String
$pipExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($pipExit -ne 0) {
    Write-Log "WARNING: pip exited with code $pipExit — may already be installed, continuing."
} else {
    Write-Log "pip completed successfully."
}

# -- Download all scripts via GitHub API ---------------------------------------
Get-GitHubFile "lifelog_extract.py"          "$INSTALL_DIR\lifelog_extract.py"
Get-GitHubFile "LifeLog-BackupService.ps1"   "$INSTALL_DIR\LifeLog-BackupService.ps1"
Get-GitHubFile "Start-LifeLog.ps1"           "$INSTALL_DIR\Start-LifeLog.ps1"
Get-GitHubFile "Update-LifeLog.ps1"          "$INSTALL_DIR\Update-LifeLog.ps1"

# -- Done ----------------------------------------------------------------------
Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "        Installation Complete!                  " -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
Write-Host "To start LifeLog services, run:" -ForegroundColor Cyan
Write-Host "  C:\ProgramData\LifeLog\Start-LifeLog.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Log "Installation complete."
