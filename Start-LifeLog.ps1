# Start-LifeLog.ps1
# Launches both the LifeLog Dev Loop and the Sonos Service in separate windows.

$LifeLogDir = "C:\ProgramData\LifeLog"
$SonosConfig = "$LifeLogDir\sonos_config.json"
$SonosService = "$LifeLogDir\sonos_service.py"
$DevLoop = "$LifeLogDir\LifeLog-DevLoop.ps1"

Write-Host "LifeLog Launcher" -ForegroundColor Cyan
Write-Host "================" -ForegroundColor Cyan

# --- Check Sonos service is installed ---
if (-not (Test-Path $SonosConfig) -or -not (Test-Path $SonosService)) {
    Write-Host ""
    Write-Host "Sonos service not installed. Running installer..." -ForegroundColor Yellow
    irm https://raw.githubusercontent.com/Shumania/lifelog/main/Install-SonosService.ps1 | iex
    Write-Host ""
}

# --- Check Dev Loop is installed ---
if (-not (Test-Path $DevLoop)) {
    Write-Host "Dev Loop not found. Downloading..." -ForegroundColor Yellow
    $headers = @{ "Accept" = "application/vnd.github.v3+json" }
    $apiUrl = "https://api.github.com/repos/Shumania/lifelog/contents/LifeLog-DevLoop.ps1"
    $response = Invoke-RestMethod -Uri $apiUrl -Headers $headers
    $content = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($response.content))
    Set-Content -Path $DevLoop -Value $content -Encoding UTF8
    Write-Host "Downloaded." -ForegroundColor Green
}

# --- Launch Dev Loop in new window ---
Write-Host ""
Write-Host "Starting LifeLog Dev Loop..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", $DevLoop -WindowStyle Normal

Start-Sleep -Seconds 1

# --- Launch Sonos Service in new window ---
Write-Host "Starting Sonos Service..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", "& python '$LifeLogDir\sonos_service.py'" -WindowStyle Normal

Write-Host ""
Write-Host "Both services started. You can close this window." -ForegroundColor Cyan
