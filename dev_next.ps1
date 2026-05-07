# dev_next.ps1 v25 - Fix installer Python stub detection + run installer
$WEBHOOK = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$machine = $env:COMPUTERNAME

# Download latest installer (cache-bust)
$installerPath = "$env:TEMP\Install-LifeLog.ps1"
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Shumania/lifelog/main/Install-LifeLog.ps1?v=$(Get-Date -Format 'yyyyMMddHHmmss')" -OutFile $installerPath -UseBasicParsing

# Run it and capture output
$output = powershell -ExecutionPolicy Bypass -File $installerPath 2>&1 | Out-String

$body = @{ computer = $machine; output = $output } | ConvertTo-Json -Compress
Invoke-WebRequest -Uri $WEBHOOK -Method POST -Body $body -ContentType "application/json" -UseBasicParsing | Out-Null
