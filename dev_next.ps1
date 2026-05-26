# Grab SSE debug lines from service log
$logFile = "C:\ProgramData\LifeLog\lifelog_service.log"
if (Test-Path $logFile) {
    $lines = Get-Content $logFile -Tail 40
    $sseLines = $lines | Where-Object { $_ -match "SSE|ntfy_ui|publish_ui" }
    if ($sseLines.Count -gt 0) {
        ($sseLines | Select-Object -Last 15) -join "`n"
    } else {
        "NO SSE LINES in last 40`n" + (($lines | Select-Object -Last 10) -join "`n")
    }
} else {
    "Log file not found"
}
