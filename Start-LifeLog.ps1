# Start-LifeLog.ps1
# Launches the unified LifeLog service (lifelog_service.py) in one window.
# Replaces the old two-service launcher (LifeLog-BackupService.ps1 + sonos_service.py).

$LifeLogDir  = "C:\ProgramData\LifeLog"
$ServiceFile = "$LifeLogDir\lifelog_service.py"
$ConfigFile  = "$LifeLogDir\lifelog_config.json"
$SonosCfg    = "$LifeLogDir\sonos_config.json"

Write-Host "LifeLog Launcher" -ForegroundColor Cyan
Write-Host "================" -ForegroundColor Cyan

# ── Download lifelog_service.py if missing ────────────────────────────────────
if (-not (Test-Path $ServiceFile)) {
    Write-Host "lifelog_service.py not found. Downloading..." -ForegroundColor Yellow
    try {
        $r = Invoke-RestMethod "https://api.github.com/repos/Shumania/lifelog/contents/lifelog_service.py" `
             -Headers @{ Accept = "application/vnd.github.v3+json"; "User-Agent" = "LifeLog-Start" }
        [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($r.content)) |
            Set-Content $ServiceFile -Encoding UTF8
        Write-Host "Downloaded via GitHub API." -ForegroundColor Green
    } catch {
        Write-Host "GitHub API failed (rate limit?). Trying raw CDN..." -ForegroundColor Yellow
        Invoke-WebRequest "https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_service.py" `
            -OutFile $ServiceFile
        Write-Host "Downloaded." -ForegroundColor Green
    }
}

# ── Create lifelog_config.json from sonos_config.json if not yet migrated ─────
if (-not (Test-Path $ConfigFile)) {
    if (Test-Path $SonosCfg) {
        Write-Host "Migrating sonos_config.json → lifelog_config.json..." -ForegroundColor Yellow
        $s = Get-Content $SonosCfg -Encoding UTF8 | ConvertFrom-Json
        $cfg = [ordered]@{
            house            = $s.house
            modules          = @("sonos","backup","dev")
            sonos_commander  = $true
        }
        $cfg | ConvertTo-Json | Set-Content $ConfigFile -Encoding UTF8
        Write-Host "Created lifelog_config.json." -ForegroundColor Green
    } else {
        Write-Host "WARNING: No config found. Service will use defaults (caphill)." -ForegroundColor Yellow
        Write-Host "Edit C:\ProgramData\LifeLog\lifelog_config.json to set house + modules." -ForegroundColor Yellow
    }
}

# ── Find Python ───────────────────────────────────────────────────────────────
$python = $null
foreach ($cmd in @("python","python3","py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ("$ver" -match "Python 3\.") { $python = $cmd; break }
    } catch {}
}
if (-not $python) {
    $found = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue |
             Select-Object -First 1
    if ($found) { $python = $found.FullName }
}
if (-not $python) {
    Write-Host "ERROR: Python 3 not found. Install Python 3.8+ and retry." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "Python: $python" -ForegroundColor Gray

# ── External rollback check (catches Python-level crashes) ────────────────────
# If a bad update makes the .py file unloadable (import error, corruption),
# the Python-internal rollback can never run. This PowerShell wrapper catches
# that: launch → wait → if it died fast AND .bak exists → restore and relaunch.
$BakFile   = "$LifeLogDir\lifelog_service.py.bak"
$FlagStart = "$LifeLogDir\update_started"
$FlagProg  = "$LifeLogDir\update_in_progress"

# Pre-launch: if rollback files already present from a previous crash, restore now
if ((Test-Path $BakFile) -and ((Test-Path $FlagStart) -or (Test-Path $FlagProg))) {
    Write-Host "ROLLBACK: Detected failed update. Restoring previous version..." -ForegroundColor Red
    Copy-Item $BakFile $ServiceFile -Force
    Remove-Item $FlagStart, $FlagProg, $BakFile -ErrorAction SilentlyContinue
    Write-Host "Rollback complete. Starting restored version." -ForegroundColor Green
}

# ── Launch unified service and monitor for rapid crash ─────────────────────────
Write-Host ""
Write-Host "Starting LifeLog Service..." -ForegroundColor Green
$proc = Start-Process $python -ArgumentList $ServiceFile `
    -PassThru -WindowStyle Normal

# Wait up to 20 seconds — if it crashes that fast, it's a bad update
$crashed = $false
if ($proc.WaitForExit(20000)) {
    # Process exited within 20 seconds — likely a crash
    if ($proc.ExitCode -ne 0) {
        $crashed = $true
    }
}

if ($crashed -and (Test-Path $BakFile)) {
    Write-Host ""
    Write-Host "RAPID CRASH DETECTED (exit code $($proc.ExitCode)). Rolling back..." -ForegroundColor Red
    Copy-Item $BakFile $ServiceFile -Force
    Remove-Item $FlagStart, $FlagProg, $BakFile -ErrorAction SilentlyContinue
    Write-Host "Rollback complete. Relaunching with previous version..." -ForegroundColor Green
    Start-Process $python -ArgumentList $ServiceFile -WindowStyle Normal
    Write-Host "Restored version started." -ForegroundColor Green
} elseif (-not $crashed) {
    Write-Host ""
    Write-Host "LifeLog service running (PID $($proc.Id)). You can close this launcher." -ForegroundColor Cyan
} else {
    Write-Host ""
    Write-Host "Service exited (exit code $($proc.ExitCode)) but no backup available." -ForegroundColor Yellow
    Write-Host "Check $LifeLogDir\lifelog_service.log for details." -ForegroundColor Yellow
}
