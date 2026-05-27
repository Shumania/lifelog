# Force-download v1.45 to bypass Unicode crash in v1.44 self-updater
$dest = 'C:\ProgramData\LifeLog\lifelog_service.py'
$url = 'https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_service.py'
Write-Host "Downloading v1.45 directly..."
Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
Write-Host "Downloaded. Restarting service..."
# Kill current python process running lifelog_service.py
Get-Process python* | Where-Object { $_.CommandLine -like '*lifelog_service*' } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Write-Host "Service stopped. It will restart via dev loop or manually."
