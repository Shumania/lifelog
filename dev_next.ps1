# Grab last 50 lines of service log looking for SSE debug output
$logFile = "C:\ProgramData\LifeLog\lifelog_service.log"
if (Test-Path $logFile) {
    $lines = Get-Content $logFile -Tail 80
    $sseLines = $lines | Select-String -Pattern "SSE|ntfy_ui|publish_ui|status_update" -SimpleMatch
    if ($sseLines.Count -gt 0) {
        $output = "=== SSE DEBUG LINES (last 80 lines of log) ===`n"
        $output += ($sseLines | ForEach-Object { $_.Line }) -join "`n"
    } else {
        $output = "=== NO SSE LINES FOUND in last 80 lines ===`n"
        $output += "Last 20 lines:`n"
        $output += ($lines | Select-Tail 20) -join "`n"
    }
    $output
} else {
    "Log file not found at $logFile"
}
