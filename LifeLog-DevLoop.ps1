# LifeLog-DevLoop.ps1
# Remote-control dev loop: polls GitHub every 5 min, runs whatever Tasklet wants, uploads output.
# Run once and leave the window open. Tasklet controls what runs via dev_next.ps1 in GitHub.

$webhookUrl = "https://webhooks.tasklet.ai/v1/public/webhook/a_1gkkvt5afqwmjxbqmr6e?token=274d4d1300bd821d855e04e51a748cb5"
$installDir = "C:\ProgramData\LifeLog"
$baseRaw = "https://raw.githubusercontent.com/Shumania/lifelog/main"
$intervalSeconds = 100  # ~1.5 minutes

Write-Host "=== LifeLog Dev Loop ===" -ForegroundColor Cyan
Write-Host "Polling every $intervalSeconds seconds. Press Ctrl+C to stop."
Write-Host ""

while ($true) {
    $ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $runTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$runTime] Fetching dev_next.ps1..." -NoNewline

    try {
        # Download the command script via GitHub API (bypasses CDN cache, always current)
        $apiUrl = "https://api.github.com/repos/Shumania/lifelog/contents/dev_next.ps1"
        $cmdScript = Invoke-RestMethod -Uri $apiUrl -Headers @{Accept="application/vnd.github.v3.raw"; "Cache-Control"="no-cache"}
        Write-Host " OK" -ForegroundColor Green

        # Execute it, capture all output
        $output = & ([scriptblock]::Create($cmdScript)) 2>&1 | Out-String

        Write-Host "  Output: $($output.Length) chars. Uploading..." -NoNewline

        # Post output to Tasklet webhook
        $body = @{
            source    = "LifeLog-DevLoop"
            computer  = $env:COMPUTERNAME
            timestamp = $runTime
            output    = $output
        } | ConvertTo-Json -Depth 3

        Invoke-RestMethod -Uri $webhookUrl -Method Post -Body $body -ContentType "application/json" | Out-Null
        Write-Host " Sent!" -ForegroundColor Green
    }
    catch {
        $errMsg = $_ | Out-String
        Write-Host " ERROR: $errMsg" -ForegroundColor Red

        # Still report errors to Tasklet
        try {
            $body = @{
                source    = "LifeLog-DevLoop"
                computer  = $env:COMPUTERNAME
                timestamp = $runTime
                output    = "ERROR: $errMsg`n`nPartial output: $output"
            } | ConvertTo-Json -Depth 3
            Invoke-RestMethod -Uri $webhookUrl -Method Post -Body $body -ContentType "application/json" | Out-Null
        } catch {}
    }

    Write-Host "  Sleeping $intervalSeconds seconds..."
    Start-Sleep -Seconds $intervalSeconds
}
