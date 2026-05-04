# Update-LifeLog.ps1
# Downloads the latest LifeLog scripts from GitHub and updates the local installation.
# Run with:
#   irm https://raw.githubusercontent.com/Shumania/lifelog/main/Update-LifeLog.ps1 | iex

$InstallDir = "C:\ProgramData\LifeLog"
$BaseUrl    = "https://raw.githubusercontent.com/Shumania/lifelog/main"
$Scripts    = @("lifelog_extract.py")

Write-Host ""
Write-Host "=== LifeLog Updater ===" -ForegroundColor Cyan
Write-Host "Install directory: $InstallDir"
Write-Host ""

# Ensure install directory exists
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Write-Host "Created $InstallDir" -ForegroundColor Yellow
}

# Download each script
$updated = @()
foreach ($script in $Scripts) {
    $dest = Join-Path $InstallDir $script
    $url  = "$BaseUrl/$script"

    Write-Host "Downloading $script ... " -NoNewline
    try {
        # Back up existing file
        if (Test-Path $dest) {
            Copy-Item $dest "$dest.bak" -Force
        }
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -ErrorAction Stop
        Write-Host "OK" -ForegroundColor Green
        $updated += $script
    } catch {
        Write-Host "FAILED: $_" -ForegroundColor Red
    }
}

Write-Host ""
if ($updated.Count -gt 0) {
    Write-Host "Updated: $($updated -join ', ')" -ForegroundColor Green
} else {
    Write-Host "No files were updated." -ForegroundColor Yellow
    exit 1
}

# Ask to run extraction now
Write-Host ""
$run = Read-Host "Run lifelog_extract.py now to sync iPhone backup data? (Y/N)"
if ($run -match '^[Yy]') {
    Write-Host ""
    Write-Host "Running extraction..." -ForegroundColor Cyan
    Set-Location $InstallDir
    python lifelog_extract.py
} else {
    Write-Host ""
    Write-Host "Done. Run 'python $InstallDir\lifelog_extract.py' whenever you're ready." -ForegroundColor Cyan
}
