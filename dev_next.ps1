# Dump last 40 log lines
Write-Output '=== LAST 40 LOG LINES ==='
if (Test-Path 'C:\ProgramData\LifeLog\lifelog_service.log') {
    Get-Content 'C:\ProgramData\LifeLog\lifelog_service.log' -Tail 40
} else {
    Write-Output 'No log file found'
    Get-ChildItem 'C:\ProgramData\LifeLog\' | ForEach-Object { $_.Name }
}
Write-Output ''
Write-Output '=== CONFIG ntfy_ui_topic ==='
try {
    $cfg = Get-Content 'C:\ProgramData\LifeLog\lifelog_config.json' -Raw | ConvertFrom-Json
    Write-Output "ntfy_ui_topic = $($cfg.ntfy_ui_topic)"
} catch {
    Write-Output "Config read error: $_"
}
Write-Output ''
Write-Output '=== DIRECT NTFY TEST ==='
try {
    $r = Invoke-WebRequest -Uri 'https://ntfy.sh/lifelog-ui-caphill-771b06' -Method POST -Body 'dev_next_test_ping' -UseBasicParsing
    Write-Output "ntfy POST status: $($r.StatusCode)"
} catch {
    Write-Output "ntfy POST failed: $_"
}
