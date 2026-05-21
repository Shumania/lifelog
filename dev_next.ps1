# Download v1.33 from GitHub and restart
$dest = 'C:\ProgramData\LifeLog\lifelog_service.py'
$url = 'https://raw.githubusercontent.com/Shumania/lifelog/main/lifelog_service.py'
Write-Output "Downloading latest lifelog_service.py..."
Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
Write-Output "Downloaded. File size: $((Get-Item $dest).Length) bytes"
Write-Output "Stopping python..."
Get-Process python* -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
Write-Output "Starting service..."
Start-Process powershell -ArgumentList '-ExecutionPolicy', 'Bypass', '-Command', "& 'C:\ProgramData\LifeLog\Start-LifeLog.ps1'" -WindowStyle Normal
Write-Output "Service restarted with v1.33"
