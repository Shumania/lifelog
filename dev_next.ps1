# dev_next.ps1 v26 - re-run installer with pip fix
$output = powershell -ExecutionPolicy Bypass -Command {
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/Install-LifeLog.ps1?v=$(Get-Date -Format 'yyyyMMddHHmmss')" -OutFile "$env:TEMP\Install-LifeLog.ps1" -UseBasicParsing
    powershell -ExecutionPolicy Bypass -File "$env:TEMP\Install-LifeLog.ps1"
} 2>&1 | Out-String
Write-Output $output
