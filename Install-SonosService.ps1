# Install-SonosService.ps1
# LifeLog Sonos Service Installer v1.0
# Downloads and configures the Sonos listening history service

$ErrorActionPreference = "Stop"
$INSTALL_DIR = "C:\ProgramData\LifeLog"
$GITHUB_RAW = "https://raw.githubusercontent.com/Shumania/lifelog/main"
$SERVICE_SCRIPT = "sonos_service.py"

Write-Host ""
Write-Host "=== LifeLog Sonos Service Installer ===" -ForegroundColor Cyan
Write-Host ""

# --- Select house ---
Write-Host "Which house is this PC in?" -ForegroundColor Yellow
Write-Host "  1) Seattle (Cap Hill)"
Write-Host "  2) Vashon (Maury House)"
Write-Host ""
$choice = Read-Host "Enter 1 or 2"
switch ($choice) {
    "1" { $HOUSE = "caphill" }
    "2" { $HOUSE = "vashon" }
    default {
        Write-Host "Invalid choice. Exiting." -ForegroundColor Red
        exit 1
    }
}
Write-Host "House set to: $HOUSE" -ForegroundColor Green

# --- Ensure install dir ---
if (-not (Test-Path $INSTALL_DIR)) {
    New-Item -ItemType Directory -Path $INSTALL_DIR -Force | Out-Null
    Write-Host "Created $INSTALL_DIR"
}

# --- Find Python (skip Windows Store stubs) ---
Write-Host ""
Write-Host "Looking for Python..." -ForegroundColor Yellow
$PYTHON = $null

# Check common install paths
$candidates = @(
    "C:\Python313\python.exe",
    "C:\Python312\python.exe",
    "C:\Python311\python.exe",
    "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python313\python.exe",
    "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python312\python.exe",
    "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python311\python.exe"
)
foreach ($p in $candidates) {
    if (Test-Path $p) { $PYTHON = $p; break }
}

# Fall back to PATH, skip Store stubs
if (-not $PYTHON) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found) {
        $path = $found.Source
        if ($path -notlike "*WindowsApps*") {
            $PYTHON = $path
        }
    }
}

if (-not $PYTHON) {
    Write-Host "Python not found. Please install Python 3.13 from https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "Run this installer again after installing Python."
    exit 1
}

Write-Host "Using Python: $PYTHON" -ForegroundColor Green

# --- Install Python dependencies ---
Write-Host ""
Write-Host "Installing Python dependencies (soco, requests)..." -ForegroundColor Yellow
& $PYTHON -m pip install soco requests --quiet --upgrade
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install failed. Check your Python installation." -ForegroundColor Red
    exit 1
}
Write-Host "Dependencies installed." -ForegroundColor Green

# --- Download sonos_service.py ---
Write-Host ""
Write-Host "Downloading sonos_service.py..." -ForegroundColor Yellow
$SERVICE_PATH = Join-Path $INSTALL_DIR $SERVICE_SCRIPT
try {
    Invoke-WebRequest -Uri "$GITHUB_RAW/$SERVICE_SCRIPT" -OutFile $SERVICE_PATH -UseBasicParsing
    Write-Host "Downloaded to $SERVICE_PATH" -ForegroundColor Green
} catch {
    Write-Host "Failed to download: $_" -ForegroundColor Red
    exit 1
}

# --- Write config ---
$CONFIG_PATH = Join-Path $INSTALL_DIR "sonos_config.json"
$config = @{ house = $HOUSE } | ConvertTo-Json
Set-Content -Path $CONFIG_PATH -Value $config -Encoding UTF8
Write-Host "Config written: $CONFIG_PATH" -ForegroundColor Green

# --- Create run batch file ---
$BAT_PATH = Join-Path $INSTALL_DIR "Run-SonosService.bat"
$batContent = "@echo off`r`necho Starting LifeLog Sonos Service ($HOUSE)...`r`n`"$PYTHON`" `"$SERVICE_PATH`"`r`npause`r`n"
Set-Content -Path $BAT_PATH -Value $batContent -Encoding ASCII
Write-Host "Run script: $BAT_PATH" -ForegroundColor Green

# --- Create desktop shortcut ---
$DESKTOP = [Environment]::GetFolderPath("Desktop")
$SHORTCUT_PATH = Join-Path $DESKTOP "LifeLog Sonos Service.lnk"
try {
    $WScriptShell = New-Object -ComObject WScript.Shell
    $shortcut = $WScriptShell.CreateShortcut($SHORTCUT_PATH)
    $shortcut.TargetPath = $BAT_PATH
    $shortcut.WorkingDirectory = $INSTALL_DIR
    $shortcut.Description = "LifeLog Sonos Service ($HOUSE)"
    $shortcut.Save()
    Write-Host "Desktop shortcut created." -ForegroundColor Green
} catch {
    Write-Host "Could not create shortcut (non-fatal): $_" -ForegroundColor Yellow
}

# --- Done ---
Write-Host ""
Write-Host "=== Installation Complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "To start the service:" -ForegroundColor Cyan
Write-Host "  Double-click 'LifeLog Sonos Service' on your desktop"
Write-Host "  OR run: $BAT_PATH"
Write-Host ""
Write-Host "The service will run in a terminal window." -ForegroundColor Yellow
Write-Host "Keep it running in the background while you're home."
Write-Host "It auto-discovers your Sonos speakers and logs what's playing."
Write-Host ""
