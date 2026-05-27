# One-shot: download v1.45 and spawn background restarter
$marker = 'C:\ProgramData\LifeLog\v145_restarted.txt'
if (Test-Path $marker) { exit 0 }

$dest = 'C:\ProgramData\LifeLog\lifelog_service.py'
$url = 'https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_service.py'

Write-Host "Downloading v1.45..."
try {
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    Write-Host "Downloaded. Spawning background restarter..."
} catch {
    Write-Host "Download failed: $_"
    exit 1
}

# Write a restart batch script
$batPath = 'C:\ProgramData\LifeLog\restart_service.bat'
@"
timeout /t 5 /nobreak >nul
taskkill /f /im python.exe 2>nul
taskkill /f /im python3.exe 2>nul
timeout /t 3 /nobreak >nul
cd /d C:\ProgramData\LifeLog
start "" python lifelog_service.py
del "%~f0"
"@ | Set-Content -Path $batPath -Encoding ASCII

# Launch it detached
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $batPath -WindowStyle Hidden

# Set marker so we don't loop
'done' | Set-Content $marker
Write-Host "Restarter launched. Service will restart in ~8 seconds on v1.45."
