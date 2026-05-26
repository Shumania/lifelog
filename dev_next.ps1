# Check SSE POST results in log
$log = 'C:\ProgramData\LifeLog\lifelog_service.log'
if (Test-Path $log) {
    $lines = Get-Content $log -Tail 200
    $sse = $lines | Select-String -Pattern 'SSE'
    if ($sse) {
        Write-Output "=== All SSE log lines (last 200) ==="
        $sse | ForEach-Object { $_.Line }
    } else {
        Write-Output "No SSE lines in last 200 log lines"
        $lines | Select-Object -Last 15
    }
} else {
    Write-Output "Log file not found"
}
