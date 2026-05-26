# Force an SSE test publish by reading ntfy_ui_topic from config
$cfg = Get-Content "C:\ProgramData\LifeLog\lifelog_config.json" | ConvertFrom-Json
Write-Output "ntfy_ui_topic from config: $($cfg.ntfy_ui_topic)"
Write-Output "house from config: $($cfg.house)"
# Also check if the topic is in the running service's output
$logFile = "C:\ProgramData\LifeLog\lifelog_service.log"
if (Test-Path $logFile) {
    $last50 = Get-Content $logFile -Tail 50
    $sseLines = $last50 | Select-String "SSE|ntfy_ui"
    if ($sseLines) {
        Write-Output "Recent SSE log lines:"
        $sseLines | ForEach-Object { Write-Output $_.Line }
    } else {
        Write-Output "No SSE-related lines in last 50 log entries"
    }
} else {
    Write-Output "No log file found"
}
