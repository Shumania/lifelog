# dev_next.ps1 v27 - run installer directly (no nested powershell block)
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy Bypass -Force

# Download and run installer directly
$installerPath = "$env:TEMP\Install-LifeLog-$(Get-Date -Format 'yyyyMMddHHmmss').ps1"
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/Install-LifeLog.ps1?v=$(Get-Date -Format 'yyyyMMddHHmmss')" -OutFile $installerPath -UseBasicParsing
& $installerPath
