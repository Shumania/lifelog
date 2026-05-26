# Grab SSE debug lines from service log
$log = "$env:ProgramData\LifeLog\lifelog_service.log"
if (Test-Path $log) {
    $lines = Get-Content $log -Tail 100 | Where-Object { $_ -match 'SSE|ntfy_ui|publish_ui' }
    if ($lines) { $lines -join "`n" } else { 'No SSE lines found in last 100 log lines' }
} else {
    'Log file not found'
}
