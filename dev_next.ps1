# Tail last 50 lines of log for SSE debug info
$log = 'C:\ProgramData\LifeLog\lifelog_service.log'
if (Test-Path $log) {
    $lines = Get-Content $log -Tail 100
    $sse = $lines | Select-String -Pattern 'SSE|ntfy_ui|publish_ui|status_update'
    if ($sse) {
        Write-Output "=== SSE-related log lines ==="
        $sse | ForEach-Object { $_.Line }
    } else {
        Write-Output "=== No SSE lines found. Last 30 lines ==="
        $lines | Select-Object -Last 30
    }
} else {
    Write-Output "Log file not found at $log"
}
