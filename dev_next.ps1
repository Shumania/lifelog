# Force restart lifelog_service.py to pick up v1.33
$restartScript = @'
Start-Sleep -Seconds 3
Get-Process python* -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
& 'C:\ProgramData\LifeLog\Start-LifeLog.ps1'
'@
$restartScript | Out-File "$env:TEMP\restart_lifelog.ps1" -Encoding UTF8
Start-Process powershell -ArgumentList '-ExecutionPolicy', 'Bypass', '-File', "$env:TEMP\restart_lifelog.ps1" -WindowStyle Normal
Write-Output "Restart scheduled - service will restart in about 5 seconds"
